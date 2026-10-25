"""Finite constrained screen using the exact passed Linux engine. No reference writes."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, sys, traceback, math
ROOT=Path(__file__).resolve().parent
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def case_issue(case):
    if not isinstance(case,dict):return 'INVALID_CASE_RECORD'
    if case.get('valid') is not True:return 'NUMERICAL_CASE_INVALID'
    metrics=case.get('metrics')
    if not isinstance(metrics,dict):return 'MISSING_METRICS'
    if not isinstance(metrics.get('observational_pass'),bool):return 'MISSING_OBSERVATIONAL_STATUS'
    value=metrics.get('J2')
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return 'INVALID_OBJECTIVE_RECORD'
    return None
def eligible_cases(cases):
    return [c for c in cases if case_issue(c) is None and c['metrics']['observational_pass']]
def record_exception(out,error):
    result_path=out/'RESULT.json'
    result=json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    count=0
    for name in ['NEW_CASES.json','VERIFICATION.json']:
        path=out/name
        if path.exists():count+=len(json.loads(path.read_text(encoding='utf-8')))
    result.update(computational_status='FAILED_OR_PREFLIGHT_BLOCK',scientific_status='INCONCLUSIVE',message=str(error),new_member_runs=count,full_history_validation=False,building_transfer_authorized=False)
    text=json.dumps(result,indent=2,allow_nan=False)
    result_path.write_text(text,encoding='utf-8')
    (out/'EXECUTION_FAILURE.json').write_text(json.dumps(dict(result,traceback=traceback.format_exc()),indent=2,allow_nan=False),encoding='utf-8')
def execute(binary,out):
    manifest=json.loads((ROOT/'RUNNER_MANIFEST.json').read_text(encoding='utf-8'))
    for name,expected in manifest.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Frozen runner input changed: '+name)
    p=module('frozen_continuation',ROOT/'p190_damage.py')
    p.prepare(ROOT/'P190_DAMAGE_INPUTS.zip',out)
    root=out/'inputs';h=p.helper(root);cfg=p.load(root/'PREREGISTRATION.json')
    plan=p.load(ROOT/'BOUNDED_PLAN.json');p.require(plan['criteria']==cfg['criteria'],'Acceptance criteria changed')
    p.require(len(plan['candidates'])==6 and plan['max_native_runs']==8 and plan['baseline_reruns']==0,'Frozen run budget changed')
    ops,provenance=h.native_import(binary,cfg);p.dump(out/'NATIVE_PROVENANCE.json',provenance)
    sys.path.insert(0,str(root/'engine'));import validation_engine as v
    p.require(v.PARAMS==cfg['source_reference_parameters'],'Frozen reference changed')
    base=p.load(root/'baseline/CASE_RESULT.json');cases=[]
    for c in plan['candidates']:
        params=dict(base['parameters']);params.update(c['parameters'])
        cyclic=dict(base['cyclic']);cyclic.update(c['cyclic'])
        p.require(.25<=params['residual_ratio']<=.65 and .06<=params['cap']<=.075 and params['peak_ratio']==1.9,'Outside finite screen bounds')
        result=h.run_case(v,root,out,cfg,c['name'],params,cyclic)
        issue=case_issue(result)
        if not isinstance(result,dict):result=dict(name=c['name'],valid=False)
        if issue:result=dict(result,numerical_status=issue)
        cases.append(result);p.dump(out/'NEW_CASES.json',cases)
        if issue:
            p.dump(out/'RESULT.json',dict(computational_status='FAILED',scientific_status='INCONCLUSIVE',numerical_status=issue,failed_case=c['name'],new_member_runs=len(cases)));raise RuntimeError(issue+': '+c['name'])
    eligible=eligible_cases(cases)
    verification=[]
    winner=min(eligible,key=lambda c:(c['metrics']['J2'],c['name'])) if eligible else None
    if winner:
        original=h.curve(out,winner['name'])
        for name,mesh,step in [('VERIFY_STEP',64,.5),('VERIFY_MESH',128,1.)]:
            r=h.run_case(v,root,out,cfg,name,winner['parameters'],winner['cyclic'],mesh=mesh,step=step)
            issue=case_issue(r)
            if not isinstance(r,dict):r=dict(name=name,valid=False)
            if issue:r=dict(r,numerical_status=issue)
            check=h.sensitivity(original,h.curve(out,name),cfg) if not issue else {'pass_check':False}
            verification.append(dict(case=r,comparison=check))
            p.dump(out/'VERIFICATION.json',verification)
            if issue:
                p.dump(out/'RESULT.json',dict(computational_status='VERIFICATION_FAILED',scientific_status='INCONCLUSIVE',numerical_status=issue,failed_case=name,new_member_runs=len(cases)+len(verification)))
                raise RuntimeError(issue+': '+name)
    robust=bool(winner and all(x['comparison']['pass_check'] and x['case']['metrics'].get('observational_pass',False) for x in verification))
    h.plots_and_tables(out,root,cases,winner['name'] if winner else None,None)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _,_,et,em=h.experiment(root)
    fig,axes=plt.subplots(3,2,figsize=(12,12))
    for ax,c in zip(axes.flat,cases):
        q=h.curve(out,c['name']);ax.plot(et,em,color='.7',lw=.6,label='Experiment geometry; order uncertain')
        ax.plot([float(r['theta_rad']) for r in q],[float(r['M_nominal_kNm']) for r in q],lw=.7,label=c['name'])
        ax.set(xlabel='Member rotation (rad)',ylabel='M (kNm)');ax.grid(alpha=.25);ax.legend(fontsize=7)
    fig.tight_layout();fig.savefig(out/'ALL_CANDIDATE_HYSTERESIS.png',dpi=150);plt.close(fig)
    p.verify(root)
    p.dump(out/'RESULT.json',dict(computational_status='COMPLETED',scientific_status=('CONDITIONAL_OBSERVATIONAL_MATCH_REQUIRES_REVIEW' if robust else 'NUMERICAL_SENSITIVITY_BLOCK') if winner else 'CALIBRATION_BLOCK',winner=winner['name'] if winner else None,new_member_runs=len(cases)+len(verification),full_history_validation=False,building_transfer_authorized=False))
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--binary-root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    try:execute(a.binary_root,a.out)
    except Exception as e:
        record_exception(a.out,e);raise
if __name__=='__main__':main()
