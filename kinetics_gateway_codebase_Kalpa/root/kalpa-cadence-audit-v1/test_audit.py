import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import audit

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');audit.init(self.db);self.cache={}
        self.rule={'vendor':'lineage','point_id':'bank.soc','substream':'ems_system','mode':'PERIODIC','interval_sec':30,'retention':'T2_1Y'}
    def tearDown(self):self.db.close()
    def emit(self,mid,stamp,**changes):
        record={'runtime_signal_id':'bank.soc','point_id':'bank.soc','vendor':'lineage','policy':'PERIODIC','central_record_sec':30,'retention_class':'T2_1Y','trigger_reason':'periodic','value':55,'quality':'good'}
        record.update(changes)
        item={'message_id':mid,'stream':'general_asset_telemetry','substream':'ems_system','created_epoch':stamp,'payload_bytes':100}
        return audit.capture(self.db,item,json.dumps({'records':[record]}),self.cache)
    def test_duplicate_message_does_not_duplicate_signal_emissions(self):
        self.emit('a',0);self.emit('a',0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM emissions').fetchone()[0],1)
    def test_periodic_early_and_excel_metadata_drift(self):
        self.emit('a',0);self.emit('b',30);self.emit('c',40,central_record_sec=5)
        signals,missing,flags,_=audit.analyse(self.db,{'points':[self.rule]})
        self.assertEqual(flags['early_periodic_candidate'],1)
        self.assertEqual(flags['excel_policy_mismatch'],1)
        self.assertEqual(missing,[])
    def test_change_allowed_before_heartbeat_and_hash_includes_quality(self):
        self.rule['mode']='ON_CHANGE_HEARTBEAT'
        self.emit('a',0,policy='ON_CHANGE_HEARTBEAT')
        self.emit('b',5,policy='ON_CHANGE_HEARTBEAT',trigger_reason='change',quality='stale')
        _,_,flags,_=audit.analyse(self.db,{'points':[self.rule]})
        self.assertNotIn('unchanged_change_candidate',flags)
        self.assertNotIn('early_periodic_candidate',flags)
    def test_late_gap_is_review_only_and_missing_point_is_explicit(self):
        self.emit('a',0);self.emit('b',100)
        missing=dict(self.rule,point_id='bank.other')
        _,unobserved,flags,_=audit.analyse(self.db,{'points':[self.rule,missing]})
        self.assertEqual(flags['late_gap_review'],1)
        self.assertEqual(len(unobserved),1)
    def test_reference_counts_and_no_raw_values_persisted(self):
        ref=audit.load(Path(__file__).with_name('reference.json'))
        self.assertEqual(len(ref['points']),2506)
        self.assertEqual(sum(r['expected_instances'] for r in ref['points']),9529)
        self.emit('a',0,value='SECRET_SENSOR_VALUE')
        self.assertNotIn('SECRET_SENSOR_VALUE','\n'.join(self.db.iterdump()))
    def test_missing_source_db_never_created(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'missing.db'
            with self.assertRaises(RuntimeError):audit.query(p,'SELECT 1')
            self.assertFalse(p.exists())
if __name__=='__main__':unittest.main()
