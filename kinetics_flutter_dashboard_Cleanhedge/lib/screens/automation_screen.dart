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
  final Map<String, bool> _pairs = {
    for (var i = 1; i <= 4; i++) 'pair_$i': true
  };
  final Map<String, TextEditingController> _chargePower = {
    for (var i = 1; i <= 4; i++) 'pair_$i': TextEditingController(text: '5')
  };
  final Map<String, TextEditingController> _dischargePower = {
    for (var i = 1; i <= 4; i++) 'pair_$i': TextEditingController(text: '5')
  };

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
    _preparationLead.text =
        timing['preparation_lead_minutes']?.toString() ?? '15';
    _dispatchLead.text =
        timing['power_dispatch_lead_seconds']?.toString() ?? '120';
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
        'charge': {
          'start': _chargeStart.text.trim(),
          'stop': _chargeStop.text.trim()
        },
        'discharge': {
          'start': _dischargeStart.text.trim(),
          'stop': _dischargeStop.text.trim()
        },
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
        'pairs': {
          for (final id in _pairs.keys)
            id: {
              'enabled': _pairs[id],
              'charge_kw': _double(_chargePower[id]!),
              'discharge_kw': _double(_dischargePower[id]!)
            }
        }
      });
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Automation settings saved')));
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.toString())));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _timeField(String label, TextEditingController controller) => SizedBox(
      width: 180,
      child: TextField(
          controller: controller,
          decoration: InputDecoration(labelText: label, hintText: 'HH:MM')));
  Widget _numberField(String label, TextEditingController controller,
          {double width = 180}) =>
      SizedBox(
          width: width,
          child: TextField(
              controller: controller,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(labelText: label)));

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
    final events = status['recent_events'] is List
        ? (status['recent_events'] as List)
            .whereType<Map>()
            .toList()
            .reversed
            .take(8)
            .toList()
        : <Map>[];
    return ListView(padding: const EdgeInsets.all(24), children: [
      Text('Solar-qualified time automation',
          style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: 8),
      const Text(
          'The gateway runs this schedule even when Flutter is closed. Stale solar data blocks starts and causes a safe stop after the configured loss delay.'),
      const SizedBox(height: 16),
      Card(
          child: Padding(
              padding: const EdgeInsets.all(16),
              child: Wrap(
                  spacing: 24,
                  runSpacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Chip(label: Text('State: ${status['phase'] ?? 'loading'}')),
                    Chip(
                        label: Text('Solar: ${status['solar_kw'] ?? '--'} kW')),
                    Chip(
                        label: Text(status['solar_fresh'] == true
                            ? 'Solar fresh'
                            : 'Solar unavailable/stale')),
                    if (prepared.isNotEmpty)
                      Chip(label: Text('Prepared: $prepared')),
                    if (active.isNotEmpty) Chip(label: Text('Active: $active')),
                    Text(status['message']?.toString() ??
                        'Waiting for supervisor status'),
                  ]))),
      SwitchListTile(
          title: const Text('Enable automatic schedule'),
          subtitle: const Text(
              'Disabling causes the supervisor to safely stop any schedule-owned operation.'),
          value: _enabled,
          onChanged: (value) => setState(() => _enabled = value)),
      Card(
          child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Daily windows',
                        style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 12),
                    Wrap(spacing: 12, runSpacing: 12, children: [
                      _timeField('Charge active by', _chargeStart),
                      _timeField('Charge stop', _chargeStop),
                      _timeField('Discharge active by', _dischargeStart),
                      _timeField('Discharge stop', _dischargeStop)
                    ]),
                    const SizedBox(height: 12),
                    Wrap(spacing: 12, runSpacing: 12, children: [
                      _numberField(
                          'Preparation lead minutes', _preparationLead),
                      _numberField('Power dispatch lead seconds', _dispatchLead)
                    ]),
                    const SizedBox(height: 8),
                    const Text(
                        'Solar qualification and zero-power preparation begin before the active-by time. Power is dispatched shortly before the boundary so all enabled pairs can verify active by the requested time.'),
                  ]))),
      Card(
          child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Solar qualification',
                        style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 12),
                    Wrap(spacing: 12, runSpacing: 12, children: [
                      _numberField('Start above kW', _startKw),
                      _numberField('Hold above seconds', _startSeconds),
                      _numberField('Stop below kW', _stopKw),
                      _numberField('Hold below seconds', _stopSeconds)
                    ]),
                  ]))),
      if (events.isNotEmpty)
        Card(
            child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Recent automation events',
                          style: Theme.of(context).textTheme.titleLarge),
                      const SizedBox(height: 8),
                      ...events.map((event) => ListTile(
                            dense: true,
                            leading: const Icon(Icons.history, size: 18),
                            title: Text(event['message']?.toString() ??
                                event['event']?.toString() ??
                                'Automation event'),
                            subtitle:
                                Text(event['timestamp']?.toString() ?? ''),
                          )),
                    ]))),
      Card(
          child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Per-pair power',
                        style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 8),
                    ..._pairs.keys.map((id) => Wrap(
                            spacing: 12,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              SizedBox(
                                  width: 130,
                                  child: CheckboxListTile(
                                      contentPadding: EdgeInsets.zero,
                                      title: Text(id
                                          .replaceFirst('_', ' ')
                                          .toUpperCase()),
                                      value: _pairs[id],
                                      onChanged: (value) => setState(
                                          () => _pairs[id] = value == true))),
                              _numberField('Charge kW', _chargePower[id]!,
                                  width: 150),
                              _numberField('Discharge kW', _dischargePower[id]!,
                                  width: 150)
                            ])),
                  ]))),
      const SizedBox(height: 12),
      FilledButton.icon(
          onPressed: _saving ? null : _save,
          icon: const Icon(Icons.save),
          label: Text(_saving ? 'Saving...' : 'Save automation settings')),
    ]);
  }
}
