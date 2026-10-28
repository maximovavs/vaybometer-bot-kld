"""KLD R5-G7 offline-only fixture regression: no API, provider or Telegram."""
import collections,hashlib,io,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
from curated_fallback_kld import _load_atlas,_asset_box,_ASSET_ORDER,select_asset
from kld_semantic_qa_facts import build_verified_facts
from kld_semantic_qa_observer import OBS_SCHEMA,REQUIRED,wrap_image_only_observer
from kld_visual_semantic_qa import evaluate_semantic_qa
from kld_visual_dedup import KLD_VISUAL_EXACT_DAYS,KLD_VISUAL_NEAR_DAYS,KLD_VISUAL_DHASH_THRESHOLD
D=json.loads((ROOT/'tools/fixtures/kld_semantic_qa_r5g6_preliminary_compact.json').read_text())
M=json.loads((ROOT/'tools/fixtures/kld_semantic_qa_r5g6_manifest_compact.json').read_text())
A={r[0]:{'i':r[1],'sha':r[2],'obs':dict(zip(D['observation_fields'],r[3:]))} for r in D['image_records']}
C={r[0]:dict(zip(D['case_fields'],r)) for r in D['case_records']}
sha=lambda b:hashlib.sha256(b).hexdigest()
def photo(name,atlas):
 a=A[name]
 if a['i']=='weekly':return (ROOT/'assets/weekly/kld'/name).read_bytes()
 assert _ASSET_ORDER[a['i']]==name
 b=io.BytesIO();atlas.crop(_asset_box(name)).save(b,format='PNG');return b.getvalue()
def facts(c):
 common={'target_date':'2026-07-20','source_status':'verified','source_id':'SYNTHETIC_OFFLINE_NOT_REAL','retrieved_at_utc':'2024-01-01T00:00:00Z'}
 win='current_morning' if c['post_type']=='morning' else 'tomorrow_morning'
 s={'weather':dict(common,window=win,weather_main=c['weather_main'],storm_signal=c['storm_signal']),
    'visibility':dict(common,window=win,visibility_condition=c['visibility_condition']),
    'lunar':dict(common,lunar_phase=c['lunar_phase'])}
 return build_verified_facts(s,target_date='2026-07-20',post_type=c['post_type'],publication_target_date='2026-07-20')
def check(c,img,obs,positive=False,invalid=False):
 obs=dict(obs)
 if positive:obs.update(moon_visibility='visible',moon_shape='full_disk',dominant_moon='no',visibility_appearance='normal',weather_appearance='clear',cloud_appearance='scattered',precipitation_appearance='none',wind_appearance='calm',sea_appearance='calm')
 unsafe=any(v in ('unclear','ambiguous') for v in obs.values()) or (obs['weather_appearance']=='cloudy' and obs['sea_appearance']=='rough' and obs['wind_appearance'] in ('strong','windy'))
 dec='ACCEPT' if positive or not unsafe else 'UNKNOWN'
 raw={'observation_schema':OBS_SCHEMA,'model_or_evaluator_version':'r5g7-manual-NOT-model','decision':dec,'reason_codes':[] if dec=='ACCEPT' else ['visual_uncertain'],'observations':obs}
 if invalid:raw['injected_forecast']='wrong'
 worker=wrap_image_only_observer(lambda image,r=raw:r)
 return evaluate_semantic_qa(worker,img,facts(c),expected_target_date='2026-07-20',expected_post_type=c['post_type'],timeout_s=3)
class OfflineAcceptance(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.atlas=_load_atlas()
 def test_01_frozen_contexts_and_eight_labels(self):
  self.assertEqual((len(A),len(C)),(20,36))
  self.assertEqual(D['status'],'OFFLINE_PRELIMINARY_NOT_HUMAN_GOLD_NOT_PROVIDER_VALIDATED')
  self.assertEqual(D['provenance']['g6_original_json_sha256'],M['original_g6_json_sha256'])
  self.assertEqual([c['case_id'] for c in C.values() if c['g2_original']!=c['g6_preliminary']],M['eight_changed_cases'])
  self.assertEqual([c['case_id'] for c in C.values() if c['seasonal_synthetic_warning']],M['synthetic_summer_snow_cases'])
  self.assertEqual(dict(collections.Counter(c['g6_preliminary'] for c in C.values())),{'ACCEPT':7,'REJECT':12,'UNKNOWN':17})
  self.assertEqual(C['G2-026']['g6_preliminary'],'REJECT');self.assertEqual(C['G2-026']['g5_visual_only'],'UNKNOWN')
  contexts=[]
  for c in C.values():
   win='current_morning' if c['post_type']=='morning' else 'tomorrow_morning'
   contexts.append({'id':c['case_id'],'asset_id':c['asset_id'],'synthetic_context':{'target_date':'2026-07-20','region':'kaliningrad','post_type':c['post_type'],'weather_main':c['weather_main'],'visibility_condition':c['visibility_condition'],'lunar_phase':c['lunar_phase'],'storm_signal':c['storm_signal'],'weather_window':win}})
  payload=json.dumps(contexts,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
  self.assertEqual(sha(payload),M['original_g2_contexts_sha256'])
 def test_02_reproducible_images(self):
  for path,expected in M['source_git_blobs'].items():
   data=(ROOT/path).read_bytes()
   self.assertEqual(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),expected,path)
  hashes=[]
  for name,a in A.items():
   image=photo(name,self.atlas);self.assertEqual(sha(image),a['sha'],name)
   with Image.open(io.BytesIO(image)) as im:im.verify()
   self.assertEqual(set(a['obs']),set(REQUIRED))
   for k,v in a['obs'].items():self.assertIn(v,REQUIRED[k])
   hashes.append(a['sha'])
  self.assertEqual(len(set(hashes)),20)
 def test_03_strict_36_safety_replays(self):
  actual=collections.Counter()
  for c in C.values():
   a=A[c['asset_id']];v=check(c,photo(c['asset_id'],self.atlas),a['obs']);actual[v.decision]+=1
   if c['g6_preliminary']!='ACCEPT':self.assertNotEqual(v.decision,'ACCEPT',c['case_id'])
  self.assertEqual(dict(actual),{'UNKNOWN':36})
 def test_04_moon_fail_closed_dedup_fallback(self):
  a=A['kld_night_01'];img=photo('kld_night_01',self.atlas)
  for case,d,r in [('G2-024','REJECT','moon_phase_conflict'),('G2-025','ACCEPT','observed_consistent'),('G2-026','REJECT','morning_moon_visible')]:
   v=check(C[case],img,a['obs'],positive=True);self.assertEqual((v.decision,v.reason),(d,r))
  self.assertEqual(check(C['G2-026'],img,a['obs']).decision,'UNKNOWN')
  a=A['kld_clear_01'];self.assertEqual(check(C['G2-002'],photo('kld_clear_01',self.atlas),a['obs'],invalid=True).decision,'UNKNOWN')
  self.assertEqual((KLD_VISUAL_EXACT_DAYS,KLD_VISUAL_NEAR_DAYS,KLD_VISUAL_DHASH_THRESHOLD),(30,14,6))
  self.assertEqual(select_asset({'date':'29.10.2026','weather':{'precipitation_display':'rain'},'actual_values':{'temp_max_c':10}},post_type='morning')[0],'rain_day')
if __name__=='__main__':unittest.main(verbosity=2)
