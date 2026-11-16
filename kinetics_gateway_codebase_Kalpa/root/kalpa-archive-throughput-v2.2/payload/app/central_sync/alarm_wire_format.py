"""S4 wire enum normalization; no changes to event identity or timestamps."""
SEVERITIES = {'critical':'Critical', 'alarm':'High', 'high':'High', 'warning':'Warning', 'info':'Info'}
STATES = {'active':'ACTIVE', 'cleared':'CLEARED', 'info':'INFO'}
def severity(value):
    key=str(value).strip().lower()
    if key not in SEVERITIES: raise ValueError('Unknown alarm severity: '+repr(value))
    return SEVERITIES[key]
def state(value):
    key=str(value).strip().lower()
    if key not in STATES: raise ValueError('Unknown alarm state: '+repr(value))
    return STATES[key]
def normalize_body(body):
    import copy
    result=copy.deepcopy(body)
    if not isinstance(result.get('records'),list): raise ValueError('Alarm records must be a list')
    for record in result['records']:
        if not isinstance(record,dict): raise ValueError('Invalid alarm record')
        record['severity']=severity(record.get('severity'))
        record['state']=state(record.get('state'))
    return result
