import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../../models/gateway_models.dart';
import '../api/gateway_api_client.dart';

class GatewayController extends ChangeNotifier {
  GatewayController({GatewayApiClient? api}) : api = api ?? GatewayApiClient();

  final GatewayApiClient api;
  final PlantSnapshot plant = PlantSnapshot();

  GatewaySession? session;
  bool busy = false;
  bool restConnected = false;
  bool wsConnected = false;
  String? errorMessage;
  String? lastEventMessage;
  final Map<String, String> lastEventMessagesByPair = <String, String>{};

  Map<String, dynamic> health = <String, dynamic>{};
  Map<String, dynamic> storage = <String, dynamic>{};
  Map<String, dynamic> polling = <String, dynamic>{};
  Map<String, dynamic> dataRate = <String, dynamic>{};
  List<Map<String, dynamic>> activeAlarms = <Map<String, dynamic>>[];
  List<Map<String, dynamic>> assetMetadata = <Map<String, dynamic>>[];

  ControlSequenceCapabilities? controlCapabilities;
  ControlSequenceStatus? controlStatus;
  final Map<String, ControlSequenceStatus> controlStatuses =
      <String, ControlSequenceStatus>{};
  Map<String, dynamic> controlPlantSummary = <String, dynamic>{};
  Map<String, dynamic> safeStopAllProgress = <String, dynamic>{};
  Map<String, dynamic> automationConfig = <String, dynamic>{};
  Map<String, dynamic> automationStatus = <String, dynamic>{};
  Map<String, dynamic> lastControlResponse = <String, dynamic>{};
  final Map<String, Map<String, dynamic>> lastControlResponsesByPair =
      <String, Map<String, dynamic>>{};
  final Set<String> _controlBusyPairs = <String>{};
  final Set<String> _automaticTrackingPairs = <String>{};
  final Map<String, Timer> _automaticTrackers = <String, Timer>{};
  final Set<String> _pairStatusInFlight = <String>{};
  final Map<String, int> _pairStatusFailures = <String, int>{};
  final Map<String, DateTime> _pairStatusRetryAfter = <String, DateTime>{};
  final Map<String, DateTime> _pairStatusReceivedAt = <String, DateTime>{};
  final Map<String, DateTime> _pairPayloadTimestamps = <String, DateTime>{};
  String selectedControlPair = 'pair_1';

  WebSocket? _telemetrySocket;
  StreamSubscription<dynamic>? _telemetrySubscription;
  Timer? _diagnosticTimer;
  Timer? _controlTimer;
  Timer? _automationTimer;
  Timer? _reconnectTimer;
  int _reconnectAttempt = 0;
  bool _controlStatusInFlight = false;
  bool _diagnosticsInFlight = false;
  int _controlStatusFailures = 0;
  DateTime? _controlStatusRetryAfter;
  bool _controlPollingEnabled = false;
  int _controlStatusGeneration = 0;
  int controlStatusSkippedRefreshes = 0;
  DateTime? controlStatusReceivedAt;
  bool _disposed = false;

  bool get authenticated => session != null;
  bool get isInternal => session?.isInternal == true;

  Future<void> login({
    required String baseUrl,
    required String username,
    required String password,
  }) async {
    busy = true;
    errorMessage = null;
    notifyListeners();
    try {
      api.configure(baseUrl: baseUrl);
      final response = await api.login(username: username, password: password);
      final token = response['access_token']?.toString();
      if (token == null || token.isEmpty) {
        throw const ApiException(
            'Login response did not contain an access token');
      }
      session = GatewaySession(
        baseUrl: api.baseUrl,
        token: token,
        username: response['username']?.toString() ?? username,
        role: response['role']?.toString() ?? 'customer',
      );
      api.configure(baseUrl: api.baseUrl, bearerToken: token);
      await bootstrap();
    } catch (error) {
      session = null;
      errorMessage = error.toString();
      rethrow;
    } finally {
      busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> bootstrap() async {
    await refreshCompact();
    await refreshDiagnostics();
    await refreshAlarms();
    await refreshAssetMetadata();
    if (isInternal) {
      await refreshControlCapabilities(silent: true);
      await refreshControlStatus(silent: true);
    }
    await connectTelemetry();
    _diagnosticTimer?.cancel();
    _diagnosticTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) {
        refreshDiagnostics(silent: true);
      },
    );
  }

  Future<void> refreshCompact({bool silent = false}) async {
    if (!silent) {
      busy = true;
      notifyListeners();
    }
    try {
      final payload = await api.getJson('/api/telemetry/compact');
      plant.applyMessage(payload);
      restConnected = true;
      errorMessage = null;
    } catch (error) {
      restConnected = false;
      errorMessage = error.toString();
      if (!silent) rethrow;
    } finally {
      if (!silent) busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> forceCompleteExtraction() async {
    busy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final payload = await api.getJson(
        '/api/telemetry/snapshot',
        query: const <String, String>{
          'refresh': 'true',
          'include_slow': 'true',
          'include_bulk': 'true',
        },
        timeout: const Duration(minutes: 5),
      );
      plant.applyMessage(payload);
      restConnected = true;
      lastEventMessage =
          'Complete fast, normal, slow and bulk extraction finished.';
    } catch (error) {
      errorMessage = error.toString();
      rethrow;
    } finally {
      busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> refreshAssetMetadata() async {
    try {
      final payload = await api.getJson('/api/assets');
      final raw = payload['assets'];
      if (raw is List) {
        assetMetadata = raw
            .whereType<Map>()
            .map((item) => Map<String, dynamic>.from(item))
            .toList();
      }
    } catch (error) {
      errorMessage = error.toString();
    }
    if (!_disposed) notifyListeners();
  }

  Future<void> refreshAlarms({bool silent = false}) async {
    try {
      final payload = await api.getJson('/api/alarms');
      final raw = payload['alarms'];
      if (raw is List) {
        activeAlarms = raw
            .whereType<Map>()
            .map((item) => Map<String, dynamic>.from(item))
            .toList();
        plant.alarms = List<Map<String, dynamic>>.from(activeAlarms);
      }
    } catch (error) {
      if (!silent) errorMessage = error.toString();
    }
    if (!_disposed) notifyListeners();
  }

  Future<void> refreshDiagnostics({bool silent = false}) async {
    if (_diagnosticsInFlight || _disposed) return;
    _diagnosticsInFlight = true;
    try {
      // Keep diagnostics single-flight and sequential. A dashboard refresh must
      // never create a burst of five competing gateway requests.
      health =
          await api.getJson('/api/health', timeout: const Duration(seconds: 5));
      storage = await api.getJson('/api/storage/status',
          timeout: const Duration(seconds: 8));
      polling = await api.getJson('/api/diagnostics/polling',
          timeout: const Duration(seconds: 8));
      dataRate = await api.getJson('/api/diagnostics/data-rate',
          timeout: const Duration(seconds: 8));
      restConnected = true;
      await refreshAlarms(silent: true);
    } catch (error) {
      restConnected = false;
      if (!silent) errorMessage = error.toString();
    } finally {
      _diagnosticsInFlight = false;
    }
    if (!_disposed) notifyListeners();
  }

  Future<AssetSnapshot> loadRackDetails(int rackId) async {
    final payload = await api.getJson(
      '/api/bms/racks/$rackId/details',
      timeout: const Duration(minutes: 5),
    );
    final asset = AssetSnapshot.fromJson(payload);
    plant.assets[asset.assetId] = asset;
    notifyListeners();
    return asset;
  }

  Future<List<Map<String, dynamic>>> loadHistorian(
    String assetId, {
    int limit = 100,
  }) async {
    final payload = await api.getJson(
      '/api/historian/$assetId',
      query: <String, String>{'limit': '$limit'},
      timeout: const Duration(seconds: 60),
    );
    final raw = payload['samples'];
    if (raw is! List) return const <Map<String, dynamic>>[];
    return raw
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
  }

  Future<List<Map<String, dynamic>>> loadAlarmHistory({int limit = 500}) async {
    final payload = await api.getJson(
      '/api/alarms/history',
      query: <String, String>{'limit': '$limit'},
    );
    final raw = payload['history'];
    if (raw is! List) return const <Map<String, dynamic>>[];
    return raw
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
  }

  Future<Map<String, dynamic>> loadMockScenarios() {
    return api.getJson('/api/mock/scenarios');
  }

  Future<void> setMockScenario(String scenario) async {
    final result = await api.postJson('/api/mock/scenario/$scenario');
    lastEventMessage = result['scenario']?.toString() == null
        ? 'Mock scenario changed to $scenario.'
        : 'Mock scenario changed to ${result['scenario']}.';
    await refreshCompact(silent: true);
  }

  Future<void> refreshControlCapabilities({bool silent = false}) async {
    if (!isInternal) return;
    try {
      final payload = await api.getJson('/api/control-sequence/capabilities');
      controlCapabilities = ControlSequenceCapabilities.fromJson(payload);
      final enabledPairs = controlCapabilities!.pairs
          .where((pair) => pair.enabled)
          .map((pair) => pair.pairId)
          .toList();
      if (enabledPairs.isNotEmpty &&
          !enabledPairs.contains(selectedControlPair)) {
        selectedControlPair = enabledPairs.first;
      }
    } catch (error) {
      if (!silent) errorMessage = error.toString();
      if (!silent) rethrow;
    } finally {
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> refreshAutomation({bool silent = false}) async {
    if (!isInternal || _disposed) return;
    try {
      final payload = await api.getJson(
        '/api/automation/schedule',
        timeout: const Duration(seconds: 10),
      );
      automationConfig = payload['config'] is Map
          ? Map<String, dynamic>.from(payload['config'] as Map)
          : <String, dynamic>{};
      automationStatus = payload['status'] is Map
          ? Map<String, dynamic>.from(payload['status'] as Map)
          : <String, dynamic>{};
      restConnected = true;
    } catch (error) {
      if (!silent) errorMessage = error.toString();
      if (!silent) rethrow;
    } finally {
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> saveAutomation(Map<String, dynamic> config) async {
    if (!isInternal) throw const ApiException('Internal role is required');
    final payload = await api.putJson('/api/automation/schedule', body: config);
    automationConfig = payload['config'] is Map
        ? Map<String, dynamic>.from(payload['config'] as Map)
        : config;
    automationStatus = payload['status'] is Map
        ? Map<String, dynamic>.from(payload['status'] as Map)
        : automationStatus;
    lastEventMessage = automationConfig['enabled'] == true
        ? 'Time and solar automation enabled.'
        : 'Time and solar automation disabled.';
    notifyListeners();
  }

  void startAutomationPolling() {
    if (!isInternal || _disposed) return;
    _automationTimer?.cancel();
    unawaited(refreshAutomation(silent: true));
    _automationTimer = Timer.periodic(
      const Duration(seconds: 3),
      (_) => unawaited(refreshAutomation(silent: true)),
    );
  }

  void stopAutomationPolling() {
    _automationTimer?.cancel();
    _automationTimer = null;
  }

  Future<void> refreshControlStatus({bool silent = false}) async {
    if (!isInternal || _disposed) return;

    // Multi-pair overview polling is cache-only and single-flight. It never
    // requests direct Modbus reads, so telemetry display cannot compete with
    // startup, safe-stop or runtime safety monitoring.
    if (_controlStatusInFlight) {
      controlStatusSkippedRefreshes += 1;
      return;
    }
    final now = DateTime.now().toUtc();
    if (_controlStatusRetryAfter?.isAfter(now) == true) {
      controlStatusSkippedRefreshes += 1;
      return;
    }

    _controlStatusInFlight = true;
    final generation = _controlStatusGeneration;
    try {
      final payload = await api.getJson(
        '/api/control-sequence/status/all/compact',
        // Normal Phase 5.7.2 responses are expected in well under one second.
        // Keep a generous transport fallback for commissioning networks while
        // single-flight prevents overlapping overview requests.
        timeout: const Duration(seconds: 30),
      );
      if (_disposed || generation != _controlStatusGeneration) return;

      final parsed = <String, ControlSequenceStatus>{};
      final byPair = payload['by_pair_id'];
      if (byPair is Map) {
        for (final entry in byPair.entries) {
          if (entry.value is Map) {
            final pairId = entry.key.toString();
            final incoming = _preserveDetailedRunSteps(
              pairId,
              Map<String, dynamic>.from(entry.value as Map),
            );
            final status = ControlSequenceStatus.fromJson(
              incoming,
            );
            parsed[pairId] = status;
          }
        }
      } else {
        final pairs = payload['pairs'];
        if (pairs is List) {
          for (final item in pairs.whereType<Map>()) {
            final incoming = Map<String, dynamic>.from(item);
            final pair = incoming['pair'];
            final pairId = pair is Map
                ? pair['pair_id']?.toString() ?? 'pair_1'
                : 'pair_1';
            final status = ControlSequenceStatus.fromJson(
              _preserveDetailedRunSteps(pairId, incoming),
            );
            parsed[status.pairId] = status;
          }
        }
      }

      for (final entry in parsed.entries) {
        final currentPayloadTime = _pairPayloadTimestamps[entry.key];
        final incomingTime = entry.value.timestamp == null
            ? null
            : DateTime.tryParse(entry.value.timestamp!);
        if (currentPayloadTime != null &&
            incomingTime != null &&
            incomingTime.toUtc().isBefore(currentPayloadTime)) {
          continue;
        }
        controlStatuses[entry.key] = entry.value;
        if (incomingTime != null) {
          _pairPayloadTimestamps[entry.key] = incomingTime.toUtc();
        }
        _pairStatusReceivedAt[entry.key] = DateTime.now().toUtc();
      }
      controlPlantSummary = payload['summary'] is Map
          ? Map<String, dynamic>.from(payload['summary'] as Map)
          : <String, dynamic>{};
      safeStopAllProgress = payload['safe_stop_all'] is Map
          ? Map<String, dynamic>.from(payload['safe_stop_all'] as Map)
          : <String, dynamic>{};
      final safeStopStatus =
          safeStopAllProgress['status']?.toString() ?? 'idle';
      if (<String>{'success', 'failed', 'idle'}.contains(safeStopStatus)) {
        _controlBusyPairs.removeAll(controlStatuses.keys);
      }
      controlStatus = controlStatuses[selectedControlPair];
      controlStatusReceivedAt = DateTime.now().toUtc();

      // The compact overview deliberately omits detailed automatic steps.
      // A scheduler-owned run may have started while Flutter was closed, so
      // there may be no local detailed copy to preserve. During an active or
      // prepared run, hydrate only the selected pair from the gateway's
      // cache-only status endpoint. This never requests fresh Modbus reads and
      // remains protected by the existing per-pair single-flight/backoff.
      final selected = controlStatus;
      final selectedSteps = selected?.runtime['steps'];
      final needsDetailedAutomaticRun = selected != null &&
          <String>{'queued', 'accepted', 'running', 'prepared'}
              .contains(selected.runStatus) &&
          (selectedSteps is! List || selectedSteps.isEmpty);
      if (needsDetailedAutomaticRun) {
        unawaited(_refreshOneControlPair(selectedControlPair, silent: true));
      }
      restConnected = true;
      _controlStatusFailures = 0;
      _controlStatusRetryAfter = null;
    } catch (error) {
      _controlStatusFailures += 1;
      _controlStatusRetryAfter = DateTime.now().toUtc().add(
            _retryDelay(_controlStatusFailures),
          );
      if (!silent) errorMessage = error.toString();
      if (!silent) rethrow;
    } finally {
      _controlStatusInFlight = false;
      if (!_disposed) notifyListeners();
    }
  }

  Map<String, dynamic> _preserveDetailedRunSteps(
    String pairId,
    Map<String, dynamic> incoming,
  ) {
    final existing = controlStatuses[pairId];
    if (existing == null) return incoming;

    final incomingRuntime = incoming['runtime'] is Map
        ? Map<String, dynamic>.from(incoming['runtime'] as Map)
        : <String, dynamic>{};
    final existingRuntime = existing.runtime;
    final incomingRunId = incomingRuntime['run_id']?.toString();
    final existingRunId = existingRuntime['run_id']?.toString();
    final incomingSteps = incomingRuntime['steps'];
    final existingSteps = existingRuntime['steps'];

    // The compact overview intentionally omits the six detailed steps. Keep
    // the last detailed copy only for the same run. A new run_id therefore
    // starts clean and can never inherit an earlier sequence's progress.
    if (incomingRunId != null &&
        incomingRunId == existingRunId &&
        (incomingSteps is! List || incomingSteps.isEmpty) &&
        existingSteps is List &&
        existingSteps.isNotEmpty) {
      incomingRuntime['steps'] = List<dynamic>.from(existingSteps);
      incoming['runtime'] = incomingRuntime;
    }
    return incoming;
  }

  Future<void> refreshSelectedControlStatusFresh({bool silent = false}) async {
    if (!isInternal || _disposed) return;
    final pairId = selectedControlPair;
    try {
      final payload = await api.getJson(
        '/api/control-sequence/$pairId/status',
        query: const <String, String>{'fresh': 'true'},
        timeout: const Duration(seconds: 120),
      );
      if (_disposed || pairId != selectedControlPair) return;
      final status = ControlSequenceStatus.fromJson(payload);
      controlStatuses[pairId] = status;
      final payloadTime = status.timestamp == null
          ? null
          : DateTime.tryParse(status.timestamp!);
      if (payloadTime != null) {
        _pairPayloadTimestamps[pairId] = payloadTime.toUtc();
      }
      _pairStatusReceivedAt[pairId] = DateTime.now().toUtc();
      controlStatus = status;
      controlStatusReceivedAt = _pairStatusReceivedAt[pairId];
      restConnected = true;
    } catch (error) {
      if (!silent) errorMessage = error.toString();
      if (!silent) rethrow;
    } finally {
      if (!_disposed) notifyListeners();
    }
  }

  void selectControlPair(String pairId) {
    if (pairId == selectedControlPair) return;
    selectedControlPair = pairId;
    controlStatus = controlStatuses[pairId];
    controlStatusReceivedAt = _pairStatusReceivedAt[pairId];
    _controlStatusGeneration += 1;
    notifyListeners();
    unawaited(refreshControlStatus(silent: true));
  }

  void startControlPolling() {
    if (!isInternal || _disposed) return;
    _controlPollingEnabled = true;
    _controlTimer?.cancel();
    _automationTimer?.cancel();
    unawaited(refreshControlStatus(silent: true));
    _controlTimer = Timer.periodic(
      const Duration(seconds: 5),
      (_) {
        if (_controlPollingEnabled) {
          unawaited(refreshControlStatus(silent: true));
        }
      },
    );
  }

  void stopControlPolling() {
    _controlPollingEnabled = false;
    _controlTimer?.cancel();
    _controlTimer = null;
  }

  bool get controlPollingActive =>
      _controlPollingEnabled && _controlTimer?.isActive == true;

  bool get controlStatusRequestActive => _controlStatusInFlight;

  bool get controlBusy => _controlBusyPairs.contains(selectedControlPair);

  bool get anyControlBusy => _controlBusyPairs.isNotEmpty;

  bool get selectedAutomaticTracking =>
      _automaticTrackingPairs.contains(selectedControlPair);

  bool isControlPairBusy(String pairId) =>
      _controlBusyPairs.contains(pairId) ||
      _automaticTrackingPairs.contains(pairId);

  Map<String, dynamic> controlResponseFor(String pairId) =>
      lastControlResponsesByPair[pairId] ?? const <String, dynamic>{};

  String? controlEventFor(String pairId) => lastEventMessagesByPair[pairId];

  ControlSequenceStatus? controlStatusFor(String pairId) =>
      controlStatuses[pairId];

  double get totalControlSetpointKw =>
      _asControllerDouble(controlPlantSummary['total_setpoint_kw']) ?? 0.0;

  double get totalControlActualPowerKw =>
      _asControllerDouble(controlPlantSummary['total_actual_power_kw']) ?? 0.0;

  String get controlStatusSource {
    final refresh = controlStatus?.raw['refresh'];
    if (refresh is Map) {
      return refresh['source']?.toString() ?? 'cached_runtime';
    }
    return 'cached_runtime';
  }

  bool get controlStatusIsStale {
    final refresh = controlStatus?.raw['refresh'];
    if (refresh is Map && refresh['stale'] == true) return true;
    final timestamp = controlStatus?.timestamp;
    final parsed = timestamp == null ? null : DateTime.tryParse(timestamp);
    if (parsed == null) return controlStatus == null;
    return DateTime.now().toUtc().difference(parsed.toUtc()) >
        const Duration(seconds: 20);
  }

  Duration? get controlStatusAge {
    final timestamp = controlStatus?.timestamp;
    final parsed = timestamp == null ? null : DateTime.tryParse(timestamp);
    if (parsed == null) return null;
    final age = DateTime.now().toUtc().difference(parsed.toUtc());
    return age.isNegative ? Duration.zero : age;
  }

  Future<Map<String, dynamic>> automaticStart({
    required String direction,
    required double targetPowerKw,
    double? rampStepKw,
    double? rampIntervalSeconds,
  }) async {
    final pairId = selectedControlPair;
    final phrase = controlCapabilities?.automaticConfirmationPhrase ??
        'EXECUTE_AUTOMATIC_SEQUENCE';
    final result = await _executeControl(
      '/api/control-sequence/$pairId/automatic-start',
      <String, dynamic>{
        'direction': direction,
        'power_kw': targetPowerKw,
        'ramp_step_kw': rampStepKw,
        'ramp_interval_seconds': rampIntervalSeconds,
        'confirmation': phrase,
      },
      successMessage:
          'Automatic $direction sequence accepted for $pairId at ${targetPowerKw.toStringAsFixed(1)} kW.',
      pairIdOverride: pairId,
      refreshAfter: false,
      timeout: const Duration(seconds: 15),
    );
    if (result['accepted'] == true) {
      _startAutomaticTracking(pairId);
      await _refreshOneControlPair(pairId, silent: true);
    }
    return result;
  }

  Future<Map<String, dynamic>> nextControlStep({
    required String direction,
    required double targetPowerKw,
  }) {
    return _executeControl(
      '/api/control-sequence/$selectedControlPair/next-step',
      <String, dynamic>{
        'direction': direction,
        'power_kw': targetPowerKw,
        'confirmation': _stageConfirmation,
      },
      successMessage: 'Commissioning step executed.',
    );
  }

  Future<Map<String, dynamic>> setControlPower({
    required String direction,
    required double powerKw,
  }) {
    return _executeControl(
      '/api/control-sequence/$selectedControlPair/set-power',
      <String, dynamic>{
        'direction': direction,
        'power_kw': powerKw,
        'confirmation': _stageConfirmation,
      },
      successMessage:
          '${direction == 'charge' ? 'Charge' : 'Discharge'} setpoint sent: ${powerKw.toStringAsFixed(1)} kW.',
    );
  }

  Future<Map<String, dynamic>> zeroControlPower() {
    return _executeControl(
      '/api/control-sequence/$selectedControlPair/zero-power',
      <String, dynamic>{'confirmation': _stageConfirmation},
      successMessage: 'Zero-power command sent and verified.',
    );
  }

  Future<Map<String, dynamic>> safeStopControl({bool openBms = true}) {
    return _executeControl(
      '/api/control-sequence/$selectedControlPair/safe-stop',
      <String, dynamic>{
        'confirmation': _stageConfirmation,
        'open_bms': openBms,
      },
      successMessage: openBms
          ? 'Safe shutdown completed; PCS stopped and BMS opened.'
          : 'PCS safe stop completed.',
      timeout: const Duration(seconds: 60),
    );
  }

  Future<Map<String, dynamic>> safeStopAllControl({bool openBms = true}) async {
    if (!isInternal) {
      throw const ApiException('Internal role is required for control');
    }
    if (_controlBusyPairs.isNotEmpty) {
      throw const ApiException(
        'Wait for active pair commands to finish before Safe Stop All.',
      );
    }
    final pairIds = controlCapabilities?.pairs
            .where((pair) => pair.enabled)
            .map((pair) => pair.pairId)
            .toSet() ??
        <String>{'pair_1', 'pair_2', 'pair_3', 'pair_4'};
    _controlBusyPairs.addAll(pairIds);
    errorMessage = null;
    notifyListeners();
    try {
      final result = await api.postJson(
        '/api/control-sequence/safe-stop-all',
        body: <String, dynamic>{
          'confirmation': _stageConfirmation,
          'open_bms': openBms,
        },
        timeout: const Duration(seconds: 20),
      );
      lastControlResponse = result;
      lastEventMessage = result['accepted'] == true
          ? 'Safe Stop All accepted; live shutdown progress is shown below.'
          : 'Safe Stop All completed with one or more pair failures.';
      await refreshControlStatus(silent: true);
      return result;
    } catch (error) {
      errorMessage = error.toString();
      rethrow;
    } finally {
      // Busy state is released by compact-status terminal progress, not by
      // the short HTTP acceptance request.
      if (!_disposed) notifyListeners();
    }
  }

  Future<Map<String, dynamic>> abortControl({bool openBms = true}) {
    return _executeControl(
      '/api/control-sequence/$selectedControlPair/abort',
      <String, dynamic>{
        'confirmation': _stageConfirmation,
        'open_bms': openBms,
      },
      successMessage: 'Sequence abort requested and safe-stop executed.',
      timeout: const Duration(seconds: 60),
    );
  }

  String get _stageConfirmation =>
      controlCapabilities?.confirmationPhrase ?? 'EXECUTE_STAGE_WRITE';

  Future<Map<String, dynamic>> _executeControl(
    String path,
    Map<String, dynamic> body, {
    required String successMessage,
    Duration timeout = const Duration(seconds: 150),
    String? pairIdOverride,
    bool refreshAfter = true,
  }) async {
    if (!isInternal) {
      throw const ApiException('Internal role is required for control');
    }
    final pairId = pairIdOverride ?? selectedControlPair;
    if (_controlBusyPairs.contains(pairId)) {
      throw ApiException(
        'A control command is already in progress for $pairId.',
      );
    }
    _controlBusyPairs.add(pairId);
    errorMessage = null;
    notifyListeners();
    try {
      body.removeWhere((key, value) => value == null);
      final result = await api.postJson(path, body: body, timeout: timeout);
      lastControlResponse = result;
      lastControlResponsesByPair[pairId] = Map<String, dynamic>.from(result);
      lastEventMessage = successMessage;
      lastEventMessagesByPair[pairId] = successMessage;
      if (refreshAfter) {
        await _refreshOneControlPair(pairId, silent: true);
      }
      return result;
    } catch (error) {
      errorMessage = error.toString();
      rethrow;
    } finally {
      _controlBusyPairs.remove(pairId);
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> _refreshOneControlPair(
    String pairId, {
    bool silent = false,
  }) async {
    if (_disposed || _pairStatusInFlight.contains(pairId)) return;
    final retryAfter = _pairStatusRetryAfter[pairId];
    if (retryAfter?.isAfter(DateTime.now().toUtc()) == true) return;
    _pairStatusInFlight.add(pairId);
    try {
      final payload = await api.getJson(
        '/api/control-sequence/$pairId/status',
        query: const <String, String>{'fresh': 'false'},
        timeout: const Duration(seconds: 12),
      );
      if (_disposed) return;
      final status = ControlSequenceStatus.fromJson(payload);
      final incomingTime = status.timestamp == null
          ? null
          : DateTime.tryParse(status.timestamp!);
      final currentPayloadTime = _pairPayloadTimestamps[pairId];
      if (currentPayloadTime == null ||
          incomingTime == null ||
          !incomingTime.toUtc().isBefore(currentPayloadTime)) {
        controlStatuses[pairId] = status;
        if (incomingTime != null) {
          _pairPayloadTimestamps[pairId] = incomingTime.toUtc();
        }
      }
      _pairStatusReceivedAt[pairId] = DateTime.now().toUtc();
      if (selectedControlPair == pairId) {
        controlStatus = status;
        controlStatusReceivedAt = _pairStatusReceivedAt[pairId];
      }
      if (!status.automaticRunning &&
          status.runStatus != 'accepted' &&
          status.runStatus != 'queued') {
        _stopAutomaticTracking(pairId);
      }
      restConnected = true;
      _pairStatusFailures.remove(pairId);
      _pairStatusRetryAfter.remove(pairId);
    } catch (error) {
      final failures = (_pairStatusFailures[pairId] ?? 0) + 1;
      _pairStatusFailures[pairId] = failures;
      _pairStatusRetryAfter[pairId] = DateTime.now().toUtc().add(
            _retryDelay(failures),
          );
      if (!silent) {
        errorMessage = error.toString();
        rethrow;
      }
    } finally {
      _pairStatusInFlight.remove(pairId);
      if (!_disposed) notifyListeners();
    }
  }

  void _startAutomaticTracking(String pairId) {
    _automaticTrackingPairs.add(pairId);
    _automaticTrackers.remove(pairId)?.cancel();
    _automaticTrackers[pairId] = Timer.periodic(
      const Duration(seconds: 2),
      (_) => unawaited(_refreshOneControlPair(pairId, silent: true)),
    );
    if (!_disposed) notifyListeners();
  }

  void _stopAutomaticTracking(String pairId) {
    _automaticTrackingPairs.remove(pairId);
    _automaticTrackers.remove(pairId)?.cancel();
  }

  Future<void> connectTelemetry() async {
    await _closeSocket();
    try {
      final socket = await api.openTelemetrySocket(mode: 'delta');
      _telemetrySocket = socket;
      wsConnected = true;
      _reconnectAttempt = 0;
      _telemetrySubscription = socket.listen(
        _handleSocketData,
        onError: (Object error, StackTrace stack) {
          wsConnected = false;
          errorMessage = 'WebSocket: $error';
          notifyListeners();
          _scheduleReconnect();
        },
        onDone: () {
          wsConnected = false;
          notifyListeners();
          _scheduleReconnect();
        },
        cancelOnError: true,
      );
    } catch (error) {
      wsConnected = false;
      errorMessage = 'WebSocket connection failed: $error';
      _scheduleReconnect();
    }
    if (!_disposed) notifyListeners();
  }

  void _handleSocketData(dynamic data) {
    try {
      final decoded = jsonDecode(data.toString());
      if (decoded is Map) {
        plant.applyMessage(Map<String, dynamic>.from(decoded));
        final type = decoded['type']?.toString();
        if (type == 'telemetry_update') {
          lastEventMessage =
              '${decoded['poll_class'] ?? 'telemetry'} update, sequence ${decoded['sequence'] ?? plant.sequence}';
        } else {
          lastEventMessage = 'Live snapshot received.';
        }
      }
      wsConnected = true;
      errorMessage = null;
    } catch (error) {
      errorMessage = 'Invalid WebSocket payload: $error';
    }
    if (!_disposed) notifyListeners();
  }

  void _scheduleReconnect() {
    if (_disposed || session == null || _reconnectTimer?.isActive == true)
      return;
    _reconnectAttempt += 1;
    final exponent = _reconnectAttempt.clamp(0, 5).toInt();
    final seconds = (1 << exponent).clamp(2, 30).toInt();
    _reconnectTimer = Timer(Duration(seconds: seconds), () {
      connectTelemetry();
    });
  }

  Future<void> _closeSocket() async {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    await _telemetrySubscription?.cancel();
    _telemetrySubscription = null;
    await _telemetrySocket?.close();
    _telemetrySocket = null;
    wsConnected = false;
  }

  Future<void> logout() async {
    _diagnosticTimer?.cancel();
    _diagnosticTimer = null;
    stopControlPolling();
    stopAutomationPolling();
    await _closeSocket();
    session = null;
    api.token = null;
    plant.assets.clear();
    activeAlarms = <Map<String, dynamic>>[];
    assetMetadata = <Map<String, dynamic>>[];
    controlCapabilities = null;
    controlStatus = null;
    controlStatuses.clear();
    _pairPayloadTimestamps.clear();
    controlPlantSummary = <String, dynamic>{};
    safeStopAllProgress = <String, dynamic>{};
    automationConfig = <String, dynamic>{};
    automationStatus = <String, dynamic>{};
    controlStatusReceivedAt = null;
    _controlBusyPairs.clear();
    _controlStatusInFlight = false;
    _diagnosticsInFlight = false;
    _controlStatusFailures = 0;
    _controlStatusRetryAfter = null;
    _pairStatusInFlight.clear();
    _pairStatusFailures.clear();
    _pairStatusRetryAfter.clear();
    _controlPollingEnabled = false;
    _controlStatusGeneration += 1;
    controlStatusSkippedRefreshes = 0;
    lastControlResponse = <String, dynamic>{};
    health = <String, dynamic>{};
    storage = <String, dynamic>{};
    polling = <String, dynamic>{};
    dataRate = <String, dynamic>{};
    restConnected = false;
    errorMessage = null;
    lastEventMessage = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _diagnosticTimer?.cancel();
    _controlTimer?.cancel();
    _automationTimer?.cancel();
    for (final timer in _automaticTrackers.values) {
      timer.cancel();
    }
    _automaticTrackers.clear();
    _reconnectTimer?.cancel();
    _telemetrySubscription?.cancel();
    _telemetrySocket?.close();
    api.close();
    super.dispose();
  }
}

double? _asControllerDouble(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '');
}

Duration _retryDelay(int failures) {
  final exponent = failures.clamp(1, 5).toInt();
  final seconds = (1 << exponent).clamp(2, 30).toInt();
  return Duration(seconds: seconds);
}
