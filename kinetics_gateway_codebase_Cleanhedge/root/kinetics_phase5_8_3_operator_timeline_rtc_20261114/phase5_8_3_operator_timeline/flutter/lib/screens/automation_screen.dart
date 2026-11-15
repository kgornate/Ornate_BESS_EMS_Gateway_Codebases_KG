import 'dart:async';

import 'package:flutter/material.dart';

import '../core/state/gateway_controller.dart';

class AutomationScreen extends StatefulWidget {
  const AutomationScreen({super.key, required this.controller});
  final GatewayController controller;

  @override
  State<AutomationScreen> createState() => _AutomationScreenState();
}

class _AutomationScreenState extends State<AutomationScreen> {
  bool _loaded = false;
  bool _saving = false;
  bool _enabled = false;
  Timer? _clockTimer;
  final ScrollController _eventScroll = ScrollController();
  String _eventFilter = 'All';
  final _chargeStart = TextEditingController(text: '09:00');
  final _chargeStop = TextEditingController(text: '14:30');
  final _dischargeStart = TextEditingController(text: '15:00');
  final _dischargeStop = TextEditingController(text: '17:00');
  final _startKw = TextEditingController(text: '100');
  final _startSeconds = TextEditingController(text: '60');
  final _stopKw = TextEditingController(text: '80');
  final _stopSeconds = TextEditingController(text: '30');
  final _preparationLead = TextEditingController(text: '15');
  final _dispatchLead = TextEditingController(text: '120');
  final Map<String, bool> _pairs = {for (var i = 1; i <= 4; i++) 'pair_$i': true};
  final Map<String, TextEditingController> _chargePower = {for (var i = 1; i <= 4; i++) 'pair_$i': TextEditingController(text: '5')};
  final Map<String, TextEditingController> _dischargePower = {for (var i = 1; i <= 4; i++) 'pair_$i': TextEditingController(text: '5')};

  @override
  void initState() {
    super.initState();
    _clockTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _clockTimer?.cancel();
    _eventScroll.dispose();
    for (final controller in <TextEditingController>[
      _chargeStart, _chargeStop, _dischargeStart, _dischargeStop,
      _startKw, _startSeconds, _stopKw, _stopSeconds,
      _preparationLead, _dispatchLead,
      ..._chargePower.values, ..._dischargePower.values,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  DateTime _atTime(DateTime day, String hhmm) {
    final parts = hhmm.split(':');
    return DateTime(day.year, day.month, day.day,
        int.tryParse(parts.first) ?? 0,
        int.tryParse(parts.length > 1 ? parts[1] : '') ?? 0);
  }

  String _duration(Duration value) {
    final seconds = value.inSeconds.abs();
    final hours = seconds ~/ 3600;
    final minutes = (seconds % 3600) ~/ 60;
    final remain = seconds % 60;
    if (hours > 0) return '${hours}h ${minutes.toString().padLeft(2, '0')}m ${remain.toString().padLeft(2, '0')}s';
    return '${minutes}m ${remain.toString().padLeft(2, '0')}s';
  }

  String _timestamp(Object? raw) {
    final value = DateTime.tryParse(raw?.toString() ?? '')?.toLocal();
    if (value == null) return 'Time unavailable';
    const months = <String>['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    final date = '${value.day.toString().padLeft(2, '0')} ${months[value.month - 1]} ${value.year}';
    final time = '${value.hour.toString().padLeft(2, '0')}:${value.minute.toString().padLeft(2, '0')}:${value.second.toString().padLeft(2, '0')}';
    return '$date  •  $time';
  }

  Map<String, String> _clockSummary(Map status) {
    final now = widget.controller.estimatedGatewayTime.toLocal();
    final phase = status['phase']?.toString() ?? '';
    final direction = status['direction']?.toString();
    final events = widget.controller.automationEvents;
    DateTime? activeSince;
    for (final event in events) {
      if (event['event'] == 'all_pairs_active' &&
          (direction == null || event['direction'] == direction)) {
        activeSince = DateTime.tryParse(event['timestamp']?.toString() ?? '')?.toLocal();
        break;
      }
    }
    if (phase.contains('active_') && direction != null) {
      final stop = _atTime(now, direction == 'charge' ? _chargeStop.text : _dischargeStop.text);
      return {
        'state': direction == 'charge' ? 'Charging' : 'Discharging',
        'elapsed': activeSince == null ? 'Starting…' : _duration(now.difference(activeSince)),
        'next': '${direction == 'charge' ? 'Charge stops' : 'Discharge stops'} in ${_duration(stop.difference(now))}',
      };
    }
    if (phase.contains('prepar') || phase.contains('qualif')) {
      final target = _atTime(now, direction == 'discharge' ? _dischargeStart.text : _chargeStart.text);
      return {'state': 'Preparing ${direction ?? 'operation'}', 'elapsed': 'In progress', 'next': 'Active-by in ${_duration(target.difference(now))}'};
    }
    final candidates = <MapEntry<String, DateTime>>[];
    for (var add = 0; add < 8; add++) {
      final day = now.add(Duration(days: add));
      final allowed = widget.controller.automationConfig['days'] is List
          ? (widget.controller.automationConfig['days'] as List).contains(day.weekday)
          : true;
      if (!allowed) continue;
      candidates.add(MapEntry('Charge', _atTime(day, _chargeStart.text)));
      candidates.add(MapEntry('Discharge', _atTime(day, _dischargeStart.text)));
    }
    candidates.removeWhere((item) => !item.value.isAfter(now));
    candidates.sort((a, b) => a.value.compareTo(b.value));
    final next = candidates.isEmpty ? null : candidates.first;
    return {
      'state': _enabled ? 'Waiting for window' : 'Automation disabled',
      'elapsed': _timestamp(now.toIso8601String()),
      'next': next == null ? 'No upcoming window' : '${next.key} starts in ${_duration(next.value.difference(now))}',
    };
  }

  bool _eventVisible(Map<String, dynamic> event) {
    final key = event['event']?.toString().toLowerCase() ?? '';
    if (_eventFilter == 'Warnings') return key.contains('fault') || key.contains('loss') || key.contains('mismatch') || key.contains('failed');
    if (_eventFilter == 'Operations') return !key.contains('telemetry_recovered') && !key.contains('snapshot_restored');
    return true;
  }

  Color _eventColor(BuildContext context, Map event) {
    final key = event['event']?.toString().toLowerCase() ?? '';
    if (key.contains('fault') || key.contains('failed')) return Theme.of(context).colorScheme.error;
    if (key.contains('loss') || key.contains('mismatch')) return Colors.orange.shade800;
    if (key.contains('complete') || key.contains('active') || key.contains('recovered')) return Colors.teal.shade700;
    return Theme.of(context).colorScheme.primary;
  }

  void _load() {
    final c = widget.controller.automationConfig;
    if (c.isEmpty) return;
    _enabled = c['enabled'] == true;
    final charge = c['charge'] is Map ? c['charge'] as Map : const {};
    final discharge = c['discharge'] is Map ? c['discharge'] as Map : const {};
    final solar = c['solar'] is Map ? c['solar'] as Map : const {};
    final timing = c['timing'] is Map ? c['timing'] as Map : const {};
    _chargeStart.text = charge['start']?.toString() ?? '09:00';
    _chargeStop.text = charge['stop']?.toString() ?? '14:30';
    _dischargeStart.text = discharge['start']?.toString() ?? '15:00';
    _dischargeStop.text = discharge['stop']?.toString() ?? '17:00';
    _startKw.text = solar['start_above_kw']?.toString() ?? '100';
    _startSeconds.text = solar['start_hold_seconds']?.toString() ?? '60';
    _stopKw.text = solar['stop_below_kw']?.toString() ?? '80';
    _stopSeconds.text = solar['stop_hold_seconds']?.toString() ?? '30';
    _preparationLead.text = timing['preparation_lead_minutes']?.toString() ?? '15';
    _dispatchLead.text = timing['power_dispatch_lead_seconds']?.toString() ?? '120';
    final pairs = c['pairs'] is Map ? c['pairs'] as Map : const {};
    for (final id in _pairs.keys) {
      final pair = pairs[id] is Map ? pairs[id] as Map : const {};
      _pairs[id] = pair['enabled'] != false;
      _chargePower[id]!.text = pair['charge_kw']?.toString() ?? '5';
      _dischargePower[id]!.text = pair['discharge_kw']?.toString() ?? '5';
    }
    _loaded = true;
  }

  double _double(TextEditingController c) => double.parse(c.text.trim());
  int _int(TextEditingController c) => int.parse(c.text.trim());

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await widget.controller.saveAutomation({
        'enabled': _enabled,
        'timezone': 'Asia/Kolkata',
        'days': [1, 2, 3, 4, 5, 6, 7],
        'charge': {'start': _chargeStart.text.trim(), 'stop': _chargeStop.text.trim()},
        'discharge': {'start': _dischargeStart.text.trim(), 'stop': _dischargeStop.text.trim()},
        'solar': {
          'start_above_kw': _double(_startKw),
          'start_hold_seconds': _int(_startSeconds),
          'stop_below_kw': _double(_stopKw),
          'stop_hold_seconds': _int(_stopSeconds),
          'max_age_seconds': 15,
        },
        'timing': {
          'preparation_lead_minutes': _int(_preparationLead),
          'power_dispatch_lead_seconds': _int(_dispatchLead),
        },
        'pairs': {for (final id in _pairs.keys) id: {'enabled': _pairs[id], 'charge_kw': _double(_chargePower[id]!), 'discharge_kw': _double(_dischargePower[id]!)}}
      });
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Automation settings saved')));
    } catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.toString())));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _timeField(String label, TextEditingController controller) => SizedBox(width: 180, child: TextField(controller: controller, decoration: InputDecoration(labelText: label, hintText: 'HH:MM')));
  Widget _numberField(String label, TextEditingController controller, {double width = 180}) => SizedBox(width: width, child: TextField(controller: controller, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: label)));

  @override
  Widget build(BuildContext context) {
    if (!_loaded && widget.controller.automationConfig.isNotEmpty) _load();
    final status = widget.controller.automationStatus;
    final prepared = status['prepared_pairs'] is List
        ? (status['prepared_pairs'] as List).join(', ')
        : '';
    final active = status['active_pairs'] is List
        ? (status['active_pairs'] as List).join(', ')
        : '';
    final events = widget.controller.automationEvents
        .where(_eventVisible)
        .toList();
    final clock = _clockSummary(status);
    return ListView(padding: const EdgeInsets.all(24), children: [
      Text('Solar-qualified time automation', style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: 8),
      const Text('The gateway runs this schedule even when Flutter is closed. Stale solar data blocks starts and causes a safe stop after the configured loss delay.'),
      const SizedBox(height: 16),
      Card(child: Padding(padding: const EdgeInsets.all(16), child: Wrap(spacing: 24, runSpacing: 12, crossAxisAlignment: WrapCrossAlignment.center, children: [
        Chip(label: Text('State: ${status['phase'] ?? 'loading'}')),
        Chip(label: Text('Solar: ${status['solar_kw'] ?? '--'} kW')),
        Chip(label: Text(status['solar_fresh'] == true ? 'Solar fresh' : 'Solar unavailable/stale')),
        if (prepared.isNotEmpty) Chip(label: Text('Prepared: $prepared')),
        if (active.isNotEmpty) Chip(label: Text('Active: $active')),
        Text(status['message']?.toString() ?? 'Waiting for supervisor status'),
      ]))),
      Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Automation timing', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 12),
        Wrap(spacing: 12, runSpacing: 12, children: [
          _TimingTile(icon: Icons.bolt, label: 'Current state', value: clock['state']!),
          _TimingTile(icon: Icons.timer_outlined, label: status['phase']?.toString().contains('active_') == true ? 'Running for' : 'Gateway time', value: clock['elapsed']!),
          _TimingTile(icon: Icons.next_plan_outlined, label: 'Next transition', value: clock['next']!),
        ]),
      ]))),
      SwitchListTile(title: const Text('Enable automatic schedule'), subtitle: const Text('Disabling causes the supervisor to safely stop any schedule-owned operation.'), value: _enabled, onChanged: (value) => setState(() => _enabled = value)),
      Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Daily windows', style: Theme.of(context).textTheme.titleLarge), const SizedBox(height: 12),
        Wrap(spacing: 12, runSpacing: 12, children: [_timeField('Charge active by', _chargeStart), _timeField('Charge stop', _chargeStop), _timeField('Discharge active by', _dischargeStart), _timeField('Discharge stop', _dischargeStop)]),
        const SizedBox(height: 12),
        Wrap(spacing: 12, runSpacing: 12, children: [_numberField('Preparation lead minutes', _preparationLead), _numberField('Power dispatch lead seconds', _dispatchLead)]),
        const SizedBox(height: 8),
        const Text('Solar qualification and zero-power preparation begin before the active-by time. Power is dispatched shortly before the boundary so all enabled pairs can verify active by the requested time.'),
      ]))),
      Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Solar qualification', style: Theme.of(context).textTheme.titleLarge), const SizedBox(height: 12),
        Wrap(spacing: 12, runSpacing: 12, children: [_numberField('Start above kW', _startKw), _numberField('Hold above seconds', _startSeconds), _numberField('Stop below kW', _stopKw), _numberField('Hold below seconds', _stopSeconds)]),
      ]))),
      Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Automation event history', style: Theme.of(context).textTheme.titleLarge),
            Text('${events.length} loaded events • newest first', style: Theme.of(context).textTheme.bodySmall),
          ])),
          IconButton(
            tooltip: 'Refresh event history',
            onPressed: widget.controller.automationEventsLoading ? null : () => widget.controller.refreshAutomationEvents(),
            icon: const Icon(Icons.refresh),
          ),
        ]),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: ['All', 'Operations', 'Warnings'].map((filter) => ChoiceChip(
          label: Text(filter),
          selected: _eventFilter == filter,
          onSelected: (_) => setState(() => _eventFilter = filter),
        )).toList()),
        const SizedBox(height: 10),
        if (events.isEmpty)
          const Padding(padding: EdgeInsets.all(24), child: Center(child: Text('No automation events available for this filter.')))
        else
          SizedBox(height: 460, child: Scrollbar(
            controller: _eventScroll,
            thumbVisibility: true,
            child: ListView.separated(
              controller: _eventScroll,
              itemCount: events.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final event = events[index];
                final color = _eventColor(context, event);
                final direction = event['direction']?.toString();
                final phase = event['phase']?.toString();
                return ListTile(
                  contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
                  leading: CircleAvatar(backgroundColor: color.withOpacity(0.12), child: Icon(Icons.history, color: color, size: 19)),
                  title: Text(event['message']?.toString() ?? event['event']?.toString() ?? 'Automation event'),
                  subtitle: Padding(padding: const EdgeInsets.only(top: 5), child: Wrap(spacing: 8, runSpacing: 5, crossAxisAlignment: WrapCrossAlignment.center, children: [
                    Text(_timestamp(event['timestamp'])),
                    if (direction != null) Chip(label: Text(direction.toUpperCase()), visualDensity: VisualDensity.compact),
                    if (phase != null) Text(phase.replaceAll('_', ' '), style: Theme.of(context).textTheme.bodySmall),
                  ])),
                );
              },
            ),
          )),
        if (widget.controller.automationEventsHasMore)
          Align(alignment: Alignment.center, child: TextButton.icon(
            onPressed: widget.controller.automationEventsLoading ? null : () => widget.controller.refreshAutomationEvents(loadOlder: true),
            icon: widget.controller.automationEventsLoading
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.expand_more),
            label: const Text('Load older events'),
          )),
      ]))),
      Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Per-pair power', style: Theme.of(context).textTheme.titleLarge), const SizedBox(height: 8),
        ..._pairs.keys.map((id) => Wrap(spacing: 12, crossAxisAlignment: WrapCrossAlignment.center, children: [SizedBox(width: 130, child: CheckboxListTile(contentPadding: EdgeInsets.zero, title: Text(id.replaceFirst('_', ' ').toUpperCase()), value: _pairs[id], onChanged: (value) => setState(() => _pairs[id] = value == true))), _numberField('Charge kW', _chargePower[id]!, width: 150), _numberField('Discharge kW', _dischargePower[id]!, width: 150)])),
      ]))),
      const SizedBox(height: 12),
      FilledButton.icon(onPressed: _saving ? null : _save, icon: const Icon(Icons.save), label: Text(_saving ? 'Saving...' : 'Save automation settings')),
    ]);
  }
}

class _TimingTile extends StatelessWidget {
  const _TimingTile({required this.icon, required this.label, required this.value});

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Container(
    width: 275,
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.primaryContainer.withOpacity(0.35),
      borderRadius: BorderRadius.circular(12),
    ),
    child: Row(children: [
      Icon(icon, color: Theme.of(context).colorScheme.primary),
      const SizedBox(width: 12),
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 3),
        Text(value, style: Theme.of(context).textTheme.titleMedium),
      ])),
    ]),
  );
}
