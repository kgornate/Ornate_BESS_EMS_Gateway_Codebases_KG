"""Stable semantic source identity, independent of ast.dump display options."""
import ast
import hashlib
import json

def tree_value(value):
    if isinstance(value,ast.AST):
        fields={name:tree_value(v) for name,v in ast.iter_fields(value)
                if not (name=='type_params' and v==[])}
        return {'node':type(value).__name__,'fields':fields}
    if isinstance(value,list):return [tree_value(v) for v in value]
    if isinstance(value,bytes):return {'bytes':value.hex()}
    if value is Ellipsis:return {'ellipsis':True}
    if isinstance(value,complex):return {'complex':[value.real,value.imag]}
    return value

def identity(node):
    raw=json.dumps(tree_value(node),sort_keys=True,separators=(',',':'),ensure_ascii=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
