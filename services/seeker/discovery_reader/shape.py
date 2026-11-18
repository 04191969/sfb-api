import json

def duplicate_guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON property: ' + key)
        result[key] = value
    return result

def parse(text):
    return json.loads(text, object_pairs_hook=duplicate_guard)

def validate_shape(value, schema, path='$'):
    t = schema['type']
    if t == 'object':
        if not isinstance(value, dict): raise ValueError(path + ': object required')
        if set(value) != set(schema['required']): raise ValueError(path + ': missing/extra fields')
        for key, sub in schema['properties'].items(): validate_shape(value[key], sub, path+'.'+key)
    elif t == 'array':
        if not isinstance(value, list): raise ValueError(path + ': array required')
        for i, v in enumerate(value): validate_shape(v, schema['items'], path+f'[{i}]')
    elif t == 'string':
        if not isinstance(value, str): raise ValueError(path + ': string required')
        if 'enum' in schema and value not in schema['enum']: raise ValueError(path + ': invalid enum')
    else: raise ValueError('Unsupported schema type')
