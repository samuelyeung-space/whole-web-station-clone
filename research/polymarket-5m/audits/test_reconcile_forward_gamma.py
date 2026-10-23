"""Counterexamples to promoting labels/fees or mismatched markets to fills."""
from copy import deepcopy
import pytest
from reconcile_forward_gamma import audit


def fixture():
    slug='btc-updown-5m-1792693800'
    gamma=[dict(slug=slug,start_sec=1792693800,end_sec=1792694100,resolved=True,
                outcome_prices=[1,0],raw={'outcomes':'["Up","Down"]',
                    'feeSchedule':{'rate':.07,'exponent':1}})]
    rows={'momentum5s.csv':dict(market_id=slug,filled='True',side='Up',won='1.0',price='.5',fee='.0175'),
          'theta30.csv':dict(market_id=slug,filled='True',side_up='True',won='True',fill_price='.5',fill_fee='.0175'),
          'paper_ledger.csv':dict(slug=slug,side='Up',won='True',ask='.5',shares='100',fee='1.75',cost='51.75'),
          'trades.csv':dict(slug=slug,side='Down',won='False',price='.5',fee='0',kind='maker')}
    p={'files':{n:dict(new_rows=[r],removed_identities=0,material_rewrites=[]) for n,r in rows.items()}}
    return p,gamma


def test_label_and_fee_match_never_attests_execution():
    result=audit(*fixture())
    assert result['used_unique_btc5m_markets']==1
    assert result['causal_book_or_real_fills_verified'] is False
    assert result['fee_effective_at_decision_verified'] is False


@pytest.mark.parametrize('mutation,match',[
    ('win','official Gamma outcome'),('fee','fee/cost arithmetic'),
    ('missing','no matching official'),('unresolved','unresolved'),
    ('duplicate','duplicate Gamma'),('rewrite','historical row rewrite'),
    ('side','invalid published side'),('filled','invalid published fill')])
def test_admission_failures(mutation,match):
    p,g=deepcopy(fixture())
    if mutation=='win':p['files']['theta30.csv']['new_rows'][0]['won']='False'
    if mutation=='fee':p['files']['paper_ledger.csv']['new_rows'][0]['fee']='.0175'
    if mutation=='missing':p['files']['momentum5s.csv']['new_rows'][0]['market_id']='btc-updown-5m-1792694100'
    if mutation=='unresolved':g[0]['resolved']=False
    if mutation=='duplicate':g.append(deepcopy(g[0]))
    if mutation=='rewrite':p['files']['trades.csv']['removed_identities']=1
    if mutation=='side':p['files']['theta30.csv']['new_rows'][0]['side_up']='unknown'
    if mutation=='filled':p['files']['theta30.csv']['new_rows'][0]['filled']='unknown'
    with pytest.raises(ValueError,match=match):audit(p,g)
