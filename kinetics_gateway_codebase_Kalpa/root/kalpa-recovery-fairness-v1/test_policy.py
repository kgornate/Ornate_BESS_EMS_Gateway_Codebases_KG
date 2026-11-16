import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from recovery_policy import RecoveryPolicy, load_policy

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.t=0.0
        self.p=RecoveryPolicy(clock=lambda:self.t)
        self.cfg=NS(max_load1=2.,min_available_memory_mb=500,
                    live_pause_sendable_count=25,live_pause_oldest_age_sec=3.)
    def run_gate(self, count=14, age=16., load=2.458, mem=1271, paused=False, available=True):
        return self.p.decide(live={'available':available,'unacked_count':count,'oldest_age_sec':age},
                             mem_mb=mem,load1=load,paused=paused,config=self.cfg,cpu_count=2)
    def advance(self, **kw):
        self.t+=5
        return self.run_gate(**kw)
    def test_observed_starvation_gets_bounded_opportunity(self):
        decisions=[self.advance() for _ in range(6)]
        self.assertFalse(any(x[0] for x in decisions[:-1]))
        self.assertTrue(decisions[-1][0]);self.assertEqual(decisions[-1][2]['mode'],'bounded_opportunity')
    def test_no_unbounded_opportunity_rate(self):
        grants=[]
        for _ in range(24):
            if self.advance()[0]:grants.append(self.t)
        self.assertEqual(grants,[30,60,90,120])
    def test_normal_gate_not_delayed(self):
        r=self.run_gate(count=0,age=None,load=1.)
        self.assertTrue(r[0]);self.assertEqual(r[2]['mode'],'normal')
    def test_manual_pause_never_overridden(self):
        self.t=3600
        for _ in range(10):self.assertFalse(self.advance(paused=True)[0])
    def test_memory_never_overridden(self):
        self.t=3600
        for _ in range(10):self.assertFalse(self.advance(mem=499)[0])
    def test_high_load_never_overridden(self):
        self.t=3600
        for _ in range(10):self.assertFalse(self.advance(load=4.01)[0])
    def test_old_live_backlog_never_overridden(self):
        self.t=3600
        for _ in range(10):self.assertFalse(self.advance(age=31)[0])
    def test_large_live_queue_never_overridden(self):
        self.t=3600
        for _ in range(10):self.assertFalse(self.advance(count=26)[0])
    def test_unavailable_live_probe(self):
        self.t=3600;self.assertFalse(self.advance(available=False)[0])
    def test_missing_age_fails_closed(self):
        self.t=3600;self.assertFalse(self.advance(age=None)[0])
    def test_nan_load_fails_closed(self):
        self.t=3600;self.assertFalse(self.advance(load=float('nan'))[0])
    def test_two_eligible_observations_required_after_pressure(self):
        self.t=100
        self.assertFalse(self.run_gate(mem=100)[0])
        self.assertFalse(self.run_gate()[0])
        self.assertTrue(self.advance()[0])
    def test_growth_defers_opportunity(self):
        self.t=100
        self.assertFalse(self.run_gate(count=10)[0])
        self.assertTrue(self.advance(count=15)[0])
        self.t+=30
        r=self.run_gate(count=25)
        self.assertFalse(r[0]);self.assertEqual(r[1],'live_queue_growing')
    def test_recovery_after_pressure_clears(self):
        self.t=100
        for _ in range(4):self.assertFalse(self.advance(count=100)[0])
        self.assertFalse(self.advance(count=10)[0]);self.assertTrue(self.advance(count=10)[0])
    def test_settings_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'policy.json'
            for value in ({'eligible_samples':1},{'max_live_age_sec':-1},{'unknown':1}, {'opportunity_interval_sec':1}, {'max_live_unacked':2.5}):
                p.write_text(json.dumps(value))
                with self.assertRaises(ValueError):load_policy(p)
    def test_patch_only_changes_intended_service_functions(self):
        tree=ast.parse(Path(__file__).with_name('overflow_recovery.py').read_text())
        svc=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='OverflowRecoveryService')
        gate=next(x for x in svc.body if isinstance(x,ast.FunctionDef) and x.name=='_resource_gate')
        cls=ast.ClassDef(name='Gate',bases=[],keywords=[],body=[gate],decorator_list=[])
        mod=ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[]))
        env={'Any':object,'os':NS(getloadavg=lambda:(2.458,0,0),cpu_count=lambda:2), '_mem_available_mb':lambda:1271}
        exec(compile(mod,'actual_gate','exec'),env)
        obj=env['Gate']();obj.recovery_policy=self.p;obj.recovery_config=self.cfg
        obj.live_probe=NS(snapshot=lambda:{'available':True,'unacked_count':14,'oldest_age_sec':16})
        obj.pause=NS(is_paused=lambda:False)
        obj.counters=NS(pause_transport_count=0,pause_memory_count=0,pause_load_count=0,
                        pause_live_count=0,pause_opportunity_count=0,bounded_opportunity_count=0)
        for _ in range(6):self.t+=5;r=obj._resource_gate()
        self.assertTrue(r[0]);self.assertEqual(obj.counters.bounded_opportunity_count,1)
        self.assertEqual(obj.counters.pause_opportunity_count,5)

if __name__=='__main__':unittest.main()
