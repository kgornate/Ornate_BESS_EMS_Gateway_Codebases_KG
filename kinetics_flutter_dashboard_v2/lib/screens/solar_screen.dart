import 'package:flutter/material.dart';

import '../core/presentation/telemetry_presentation.dart';
import '../core/state/gateway_controller.dart';
import '../models/gateway_models.dart';
import '../widgets/common_widgets.dart';

class SolarScreen extends StatelessWidget {
  const SolarScreen({super.key, required this.controller});

  final GatewayController controller;

  @override
  Widget build(BuildContext context) {
    final asset = controller.plant.solar;
    if (asset == null) {
      return RefreshIndicator(
        onRefresh: controller.refreshCompact,
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: const [
            SectionHeader(
              'Solar Plant / PCC',
              subtitle: 'Waiting for the solar_plant asset from the gateway.',
            ),
            SizedBox(height: 16),
            Card(
              child: ListTile(
                leading: Icon(Icons.solar_power_outlined),
                title: Text('No solar telemetry received yet'),
              ),
            ),
          ],
        ),
      );
    }

    final grouped = <String, List<MapEntry<String, TelemetryPoint>>>{};
    for (final entry in asset.telemetry.entries) {
      grouped.putIfAbsent(categoryTitle(entry.key, entry.value), () => []).add(entry);
    }
    const order = <String>[
      'Hopewind solar plant',
      'ABB grid meter / PCC',
      'Energy counters',
      'Inverter and array status',
      'Hopewind diagnostic PCC values',
      'Electrical measurements',
    ];
    final sections = grouped.entries.toList()
      ..sort((a, b) {
        final ai = order.indexOf(a.key);
        final bi = order.indexOf(b.key);
        return (ai < 0 ? order.length : ai)
            .compareTo(bi < 0 ? order.length : bi);
      });

    return RefreshIndicator(
      onRefresh: controller.refreshCompact,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          SectionHeader(
            'Solar Plant / PCC',
            subtitle:
                'Hopewind generation and ABB grid-meter telemetry • ${asset.timestamp ?? 'No timestamp'}',
            trailing: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                StatusPill(
                  label: asset.online ? 'Online' : 'Offline',
                  good: asset.online,
                ),
                StatusPill(
                  label: _text(asset, 'grid_meter_source', fallback: 'Grid meter unavailable'),
                  good: _text(asset, 'grid_meter_source').isNotEmpty,
                  icon: Icons.speed,
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          MetricGrid(
            children: [
              _metric(asset, 'solar_active_power_kw', 'Solar generation', Icons.solar_power, emphasis: true),
              _metric(asset, 'pcc_export_power_kw', 'Grid export', Icons.upload, emphasis: true),
              _metric(asset, 'grid_import_power_kw', 'Grid import', Icons.download, emphasis: true),
              _metric(asset, 'site_load_power_kw', 'Calculated site load', Icons.factory),
              _metric(asset, 'grid_frequency_hz', 'Grid frequency', Icons.waves),
              _metric(asset, 'grid_line_voltage_v', 'Grid line voltage', Icons.bolt),
              _metric(asset, 'grid_average_current_a', 'Grid current', Icons.electric_meter),
              _metric(asset, 'meter_power_factor', 'Power factor', Icons.speed),
              _metric(asset, 'inverters_online', 'Inverters online', Icons.hub),
              _metric(asset, 'inverters_faulted', 'Inverters faulted', Icons.warning_amber),
              _metric(asset, 'total_export_energy_kwh', 'Export energy', Icons.timeline),
              _metric(asset, 'total_grid_import_energy_kwh', 'Import energy', Icons.timeline),
            ],
          ),
          const SizedBox(height: 24),
          ...sections.map(
            (section) => TelemetrySection(
              title: section.key,
              asset: asset,
              entries: section.value,
              initiallyExpanded: section.key != 'Hopewind diagnostic PCC values',
            ),
          ),
          const SizedBox(height: 16),
          Card(
            clipBehavior: Clip.antiAlias,
            child: ExpansionTile(
              title: const Text('All solar and grid signals',
                  style: TextStyle(fontWeight: FontWeight.w800)),
              childrenPadding: const EdgeInsets.fromLTRB(12, 8, 12, 16),
              children: [EngineeringTable(asset: asset, maxHeight: 620)],
            ),
          ),
          const SizedBox(height: 40),
        ],
      ),
    );
  }

  static Widget _metric(
    AssetSnapshot asset,
    String key,
    String label,
    IconData icon, {
    bool emphasis = false,
  }) {
    final point = asset.telemetry[key];
    final shown = point == null
        ? const PresentedValue(value: '--', unit: '', valid: false)
        : presentPoint(asset.assetType, key, point);
    return MetricTile(
      label: label,
      value: shown.text,
      subtitle: point == null ? 'Signal unavailable' : shown.note,
      icon: icon,
      emphasis: emphasis,
    );
  }

  static String _text(
    AssetSnapshot asset,
    String key, {
    String fallback = '',
  }) {
    final value = asset.telemetry[key]?.value?.toString();
    return value == null || value.isEmpty ? fallback : value;
  }
}
