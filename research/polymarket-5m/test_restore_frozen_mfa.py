import copy,hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from restore_frozen_mfa import enrich,restore,sha,validate_recipe

class Tests(unittest.TestCase):
    def test_no_outcome_read(self):
        e={'type':'outcome','winner':'Up'};self.assertEqual(enrich(copy.deepcopy(e),{}, {},{}),e)
    def test_no_carry_or_future_score(self):
        e={'type':'decision','market_id':'x','recv_ms':1000,'features':{},'feature_available_ms':{}}
        out=enrich(e,{('x',2000):{'mfa_trend_fixed':.8}}, {},{})
        self.assertEqual(out['features'],{})
    def test_saved_score_available_at_original_decision_only(self):
        e={'type':'decision','market_id':'x','recv_ms':1000,'features':{},'feature_available_ms':{}}
        out=enrich(e,{('x',1000):{'mfa_trend_fixed':.8}}, {},{})
        self.assertEqual(out['features'],{'mfa_trend_fixed':.8});self.assertEqual(out['feature_available_ms'],{'mfa_trend_fixed':1000})
    def test_collision_rejected(self):
        e={'type':'decision','market_id':'x','recv_ms':1,'features':{'q':.1},'feature_available_ms':{'q':1}}
        with self.assertRaises(ValueError):enrich(e,{('x',1):{'q':.8}}, {},{})
    def test_header_is_checked_not_blindly_replaced(self):
        raw={'type':'dataset','dataset_id':'a','split':'development','recv_ms':0}
        header={**raw,'dataset_id':'a:MFA-v1','mfa_registration_sha256':'frozen'}
        self.assertEqual(enrich(copy.deepcopy(raw),{}, {},header),header)
        header['recv_ms']=1
        with self.assertRaises(ValueError):enrich(copy.deepcopy(raw),{}, {},header)
    def test_other_split_and_duplicate_sources_rejected(self):
        r={'schema':'frozen-mfa-restoration-v1','role':'already_consumed_development',
            'sources':[{'member':'a','header':{'split':'development'}}],
            'target_manifest':{'split':'development'}}
        validate_recipe(r);bad=copy.deepcopy(r);bad['sources']*=2
        with self.assertRaises(ValueError):validate_recipe(bad)
        bad=copy.deepcopy(r);bad['target_manifest']['split']='oos'
        with self.assertRaises(ValueError):validate_recipe(bad)
    def test_wrong_archive_fails_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'raw').write_bytes(b'bad')
            recipe={'schema':'frozen-mfa-restoration-v1','role':'already_consumed_development',
                'sources':[{'member':'a','header':{'split':'development'}}],
                'target_manifest':{'split':'development'},'archive_sha256':'wrong'}
            (p/'recipe').write_text(json.dumps(recipe))
            with self.assertRaisesRegex(ValueError,'archive hash mismatch'):restore(p/'raw',p/'unused',p/'recipe',p/'out')
            self.assertFalse((p/'out').exists())
    def test_wrong_score_bank_fails_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'raw').write_bytes(b'archive')
            with zipfile.ZipFile(p/'pack','w') as z:z.writestr('scores',b'tampered')
            recipe={'schema':'frozen-mfa-restoration-v1','role':'already_consumed_development',
                'sources':[{'member':'a','header':{'split':'development'}}],
                'target_manifest':{'split':'development'},'archive_sha256':sha(p/'raw'),
                'execution_projection_code_sha256':sha(Path(__file__).with_name('minimal_feature_research.py')),
                'scores_member':'scores','scores_sha256':'wrong'}
            (p/'recipe').write_text(json.dumps(recipe))
            with self.assertRaisesRegex(ValueError,'score bank mismatch'):restore(p/'raw',p/'pack',p/'recipe',p/'out')
            self.assertFalse((p/'out').exists())

if __name__=='__main__':unittest.main()
