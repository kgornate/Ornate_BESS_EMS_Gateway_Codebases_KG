"""Bounded archive bandwidth and trend-based live protection; no queue writes."""
from collections import deque
import json
import math
from pathlib import Path
import threading
import time

SETTINGS_PATH='/etc/ornate-ems-gateway/archive_recovery_budget.json'
DEFAULTS={
    'initial_MB_per_min':2.0, 'max_MB_per_min':10.0,
    'min_MB_per_min':0.25, 'limited_MB_per_min':0.5,
    'increase_MB_per_min':0.5, 'increase_interval_sec':60.0,
    'burst_bytes':262400, 'poll_interval_sec':1.0,
    'soft_live_count':25, 'soft_live_age_sec':30.0,
    'hard_live_count':100, 'hard_live_age_sec':75.0,
    'trend_window_sec':30.0, 'growth_messages':10,
    'healthy_samples':2, 'live_failure_cooldown_sec':30.0,
    'live_status_max_age_sec':10.0,
}

def load_budget_settings(path=SETTINGS_PATH):
    result=dict(DEFAULTS)
    if Path(path).is_file():
        extra=json.loads(Path(path).read_text())
        if not isinstance(extra,dict) or set(extra)-set(result):
            raise ValueError('Unknown archive budget settings')
        result.update(extra)
    for key,value in result.items():
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError('Invalid archive budget setting: '+key)
    for key in ('burst_bytes','soft_live_count','hard_live_count','growth_messages','healthy_samples'):
        if int(result[key])!=result[key]: raise ValueError('Integer required: '+key)
    if not (result['min_MB_per_min']<=result['initial_MB_per_min']<=result['max_MB_per_min']):
        raise ValueError('Invalid archive rate bounds')
    if not (result['min_MB_per_min']<=result['limited_MB_per_min']<=result['max_MB_per_min']):
        raise ValueError('Invalid limited archive rate')
    if result['healthy_samples']<2 or result['poll_interval_sec']<1 or result['increase_interval_sec']<30:
        raise ValueError('Observation/ramp interval too small')
    if result['soft_live_count']>result['hard_live_count'] or result['soft_live_age_sec']>result['hard_live_age_sec']:
        raise ValueError('Soft pressure bounds exceed hard pressure bounds')
    return result


class ByteBudget:
    """Charge attempted logical bytes, including HTTP retries and split POSTs."""
    def __init__(self,capacity,clock=time.monotonic):
        self.capacity=int(capacity); self.clock=clock; self.rate=0.0
        self.tokens=0.0; self.updated=clock(); self.charged_bytes=0; self._lock=threading.Lock()
    def _refill(self):
        now=self.clock(); elapsed=max(0.0,now-self.updated)
        self.tokens=min(self.capacity,self.tokens+elapsed*self.rate)
        self.updated=now
    def set_rate(self,MB_per_min):
        with self._lock:
            self._refill(); self.rate=float(MB_per_min)*1e6/60
            # Pauses cannot accumulate a burst to be released on recovery.
            if self.rate<=0: self.tokens=min(0.0,self.tokens)
    def ready(self,size):
        with self._lock:
            self._refill()
            return self.rate>0 and self.tokens+1e-6>=min(int(size),self.capacity)
    def consume(self,size):
        with self._lock:
            self._refill(); size=int(size)
            if size<0: raise ValueError('Negative request size')
            if self.rate<=0 or self.tokens+1e-6<min(size,self.capacity): return False
            # Preserve the existing oversized-first-message behavior, then
            # repay any debt before another request. No message is dropped.
            self.tokens-=size; self.charged_bytes+=size; return True
    def status(self):
        with self._lock:
            self._refill()
            return {'MB_per_min':round(self.rate*60/1e6,3),
                    'available_bytes':int(self.tokens),'burst_bytes':self.capacity,
                    'attempted_logical_bytes_charged':self.charged_bytes}


class BudgetPolicy:
    def __init__(self,legacy,settings,budget,clock=time.monotonic):
        self.legacy=legacy; self.settings=settings; self.budget=budget; self.clock=clock
        self.cooldown_until=0.0; self.cooldown_reason='archive_transport_cooldown'; self.streak=0; self.mode='settling'
        self.delay=settings['poll_interval_sec']; self.samples=deque(maxlen=max(64,int(settings['trend_window_sec']/settings['poll_interval_sec'])*2+4))
        self.last_observation=None; self.last_failure_counter=None; self.live_instance=None
        self.rate=settings['initial_MB_per_min']; self.last_increase=clock()
    def cooldown(self,seconds,reason='archive_transport_cooldown'):
        seconds=float(seconds)
        if not math.isfinite(seconds): seconds=self.legacy['failure_cooldown_sec']
        until=self.clock()+max(0,seconds)
        if until>=self.cooldown_until:
            self.cooldown_until=until; self.cooldown_reason=reason
        self.rate=max(self.settings['min_MB_per_min'],self.rate/2)
        self.budget.set_rate(0)
    def hard_reason(self,health,config):
        live=health.get('live') or {}; transport=health.get('live_transport') or {}
        if health.get('transport_paused'): return 'transport_paused'
        if not live.get('available'): return 'live_probe_unavailable'
        if not transport.get('available'): return 'live_status_unavailable'
        if transport.get('last_error'): return 'live_transport_failure'
        try:
            memory=float(health['mem_available_mb']); load=float(health['load1'])
            count=float(live['unacked_count']); age=live.get('oldest_age_sec')
            cpus=float(health['cpu_count'])
            if not all(math.isfinite(x) for x in (memory,load,count,cpus)) or cpus<1 or load<0 or count<0 or int(count)!=count:
                return 'invalid_health'
            if memory<config.min_available_memory_mb: return 'memory_pressure'
            if load>cpus*self.legacy['max_load_per_cpu']: return 'load_pressure'
            if count>self.settings['hard_live_count']: return 'live_queue_hard_limit'
            if count and age is None: return 'live_age_unavailable'
            if age is not None and (not math.isfinite(float(age)) or float(age)<0): return 'live_age_unavailable'
            if age is not None and float(age)>self.settings['hard_live_age_sec']: return 'live_age_hard_limit'
        except (KeyError,TypeError,ValueError,OverflowError): return 'invalid_health'
        if self.clock()<self.cooldown_until: return self.cooldown_reason
        return None
    def decide(self,health,config):
        now=self.clock(); t=health.get('live_transport') or {}
        instance=t.get('started_utc'); failures=t.get('request_failure_count')
        if instance!=self.live_instance:
            self.live_instance=instance; self.last_failure_counter=failures
            self.samples.clear(); self.streak=0
        elif isinstance(failures,int) and isinstance(self.last_failure_counter,int):
            if failures>self.last_failure_counter:
                self.cooldown(self.settings['live_failure_cooldown_sec'],reason='live_transport_cooldown')
            self.last_failure_counter=failures
        reason=self.hard_reason(health,config)
        fresh=self.last_observation is None or now-self.last_observation>=1.0
        if reason:
            self.streak=0; self.mode='yield'; self.budget.set_rate(0)
            self.samples.clear()
            self.last_increase=now
            return False,reason
        count=int(health['live']['unacked_count']); age=health['live'].get('oldest_age_sec') or 0
        if fresh:
            self.last_observation=now; self.samples.append((now,count,float(age)))
            window=self.settings['trend_window_sec']
            # Keep enough history to select a baseline AT OR BEFORE the
            # window boundary. Jitter must not prevent a full-window test.
            while self.samples and now-self.samples[0][0]>2*window: self.samples.popleft()
            baseline=next((x for x in reversed(self.samples) if x[0]<=now-window),None)
            history=[x for x in self.samples if baseline is not None and x[0]>=baseline[0]]
            sustained=(baseline is not None and len(history)>=3 and
                all(x[1]>=self.settings['soft_live_count'] and
                    x[2]>=self.settings['soft_live_age_sec'] for x in history))
            if (sustained and count-baseline[1]>=self.settings['growth_messages'] and
                float(age)-baseline[2]>=10.0):
                self.samples.clear(); self.streak=0; self.mode='yield'
                self.cooldown(10,reason='live_pressure_cooldown')
                return False,'live_queue_growing_and_aging'
            self.streak+=1
        if self.streak<self.settings['healthy_samples']:
            self.mode='settling'; self.budget.set_rate(0)
            return False,'healthy_observation_wait'
        fast=count<=self.settings['soft_live_count'] and age<=self.settings['soft_live_age_sec']
        if fast:
            if now-self.last_increase>=self.settings['increase_interval_sec']:
                self.rate=min(self.settings['max_MB_per_min'],self.rate+self.settings['increase_MB_per_min'])
                self.last_increase=now
            self.mode='catch_up'; self.budget.set_rate(self.rate)
        else:
            self.mode='limited'; self.last_increase=now
            self.budget.set_rate(min(self.rate,self.settings['limited_MB_per_min']))
        return True,None
