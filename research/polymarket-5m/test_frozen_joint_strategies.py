"""Exact frozen source extraction plus numerical rule parity, no model fitting."""
import ast,hashlib,importlib,random,unittest
from pathlib import Path


def check(source,target,provider):
 text=(Path(__file__).parent/source).read_text();tree=ast.parse(text)
 functions={n.name:ast.get_source_segment(text,n) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in (provider,'signal')}
 plugin=importlib.import_module(target);assert plugin.PROVENANCE['original_source_sha256']==hashlib.sha256(text.encode()).hexdigest()
 actual=Path(plugin.__file__).read_text();actualtree=ast.parse(actual)
 for node in actualtree.body:
  if isinstance(node,ast.FunctionDef):
   body=ast.get_source_segment(actual,node)
   assert body==functions[node.name]
   assert hashlib.sha256(body.encode()).hexdigest()==plugin.PROVENANCE['function_sha256'][node.name]
 scope={};exec('\n\n'.join(functions.values()),scope)
 candidates=scope[provider]();assert candidates==getattr(plugin,provider)()
 random.seed(234)
 for c in candidates:
  for _ in range(10):
   f={n:random.uniform(-2,2) for n in c['required_features']}
   f.update(tau_s=random.uniform(0,300),ask_up=.4,ask_down=.6,fee=.07)
   assert scope['signal'](c,f)==plugin.signal(c,f)
 return len(candidates)


class FrozenStrategyTests(unittest.TestCase):
 def test_jpr(self):self.assertEqual(check('joint_probability_research.py','frozen_jpr_strategy','catalog'),96)
 def test_mar(self):self.assertEqual(check('mean_regime_research.py','frozen_mar_strategy','candidates'),144)

if __name__=='__main__':unittest.main()
