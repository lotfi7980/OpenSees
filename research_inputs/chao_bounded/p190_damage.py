"""Continuation of completed P190_C060: at most four damage-only cases.
No baseline rerun, material/build/mesh test, optimization or backbone change.
Upload to .github/scripts/p190_damage.py.
"""
from pathlib import Path
import argparse,csv,hashlib,importlib.util,json,sys,traceback,zipfile
PAYLOAD_SHA256='2b7e75bc4c33075a56f98421f305f2ad5a8a37ac3177fd8b8a8c37ab773cf3ba'
def require(ok,message):
    if not ok:raise RuntimeError(message)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def standard(value):
    try:import numpy as np
    except ImportError:np=None
    def cv(x):
        if np is not None:
            if isinstance(x,np.bool_):return bool(x)
            if isinstance(x,np.integer):return int(x)
            if isinstance(x,np.floating):return float(x)
            if isinstance(x,np.ndarray):return cv(x.tolist())
        if isinstance(x,dict):return {cv(k):cv(v) for k,v in x.items()}
        if isinstance(x,(list,tuple)):return [cv(v) for v in x]
        return x
    return cv(value)
def dump(p,x):p.write_text(json.dumps(standard(x),indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def table(p,rows):
    if not rows:return
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader()
        w.writerows({k:json.dumps(standard(v),allow_nan=False) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()} for r in rows)
def verify(root):
    for name,expected in load(root/'PAYLOAD_MANIFEST.json').items():require(sha(root/name)==expected,'Frozen continuation input changed: '+name)
def prepare(payload,out):
    require(sha(payload)==PAYLOAD_SHA256,'Wrong continuation payload')
    root=out/'inputs';root.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(payload) as z:
        for item in z.infolist():
            p=(root/item.filename).resolve();require(p.is_relative_to(root.resolve()),'Unsafe archive path')
            if item.is_dir():p.mkdir(parents=True,exist_ok=True)
            else:p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(item))
    verify(root);plan=load(root/'DAMAGE_SENSITIVITY_PLAN.json');base=load(root/'baseline/CASE_RESULT.json')
    require(base['valid'] and base['status']['completed_excursions']==66,'Archived P190_C060 not completed')
    require(base['parameters']==plan['fixed_parameters'] and base['parameters']['peak_ratio']==1.9 and base['parameters']['cap']==.06,'Wrong fixed backbone')
    require(plan['maximum_new_member_runs']==4 and len(plan['candidates'])==4,'Candidate budget changed')
    dump(out/'FROZEN_PLAN.json',plan)
    dump(out/'PREPARATION.json',dict(payload_sha256=PAYLOAD_SHA256,baseline='REUSED_COMPLETED_RUN_40202450657',baseline_reruns=0,new_native_runs=0))
def helper(root):
    plan=load(root/'DAMAGE_SENSITIVITY_PLAN.json');p=root/'legacy_calibration.py'
    require(sha(p)==plan['legacy_runner_sha256'],'Actual executed legacy runner changed')
    spec=importlib.util.spec_from_file_location('p190_executed_runner',p);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    return h
def endpoint_map(rows):
    result={}
    for r in rows:result[int(r['excursion'])]=r
    return result
def diagnostics(case,rows,base_rows,plan):
    a,b=endpoint_map(rows),endpoint_map(base_rows)
    require(all(i in a and i in b for i in range(1,67)),'Incomplete 66-excursion continuation')
    delta=[];signs=[]
    for i in range(1,37):
        x,y=float(a[i]['M_nominal_kNm']),float(b[i]['M_nominal_kNm'])
        require(abs(y)>1e-9,'Zero archived early endpoint')
        delta.append(abs(x-y)/abs(y));signs.append(x*y>0)
    early=max(delta)<=plan['early_preservation']['relative_endpoint_change_limit'] and all(signs)
    tail=max(abs(x) for x in case['metrics']['tail_retention_errors'])<=plan['inherited_criteria']['tail_endpoint_retention_abs_error']
    return dict(max_early_endpoint_change_relative_to_P190=max(delta),early_signs_preserved=all(signs),
        early_preserved=early,tail_retention_pass=tail,early_and_tail_goal=bool(case['valid'] and early and tail),
        all_inherited_observational_criteria=case['metrics']['observational_pass'])
def export(out,root,cases):
    import numpy as np
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    base=load(root/'baseline/CASE_RESULT.json');baseline=read(root/'baseline/RESPONSE.csv');plan=load(root/'DAMAGE_SENSITIVITY_PLAN.json')
    protocol=read(root/'HALF_CYCLES_FINAL.csv');exp=read(root/'engine/inputs/EXPERIMENT_DIGITIZATION.csv')
    et=[float(r['rotation_rad']) for r in exp];em=[float(r['moment_kNm']) for r in exp]
    curves={'ARCHIVED_P190_C060':baseline}
    results=[dict(base,source='ARCHIVED_RUN_40202450657',diagnostic=dict(max_early_endpoint_change_relative_to_P190=0.,early_preserved=True,tail_retention_pass=False,early_and_tail_goal=False))]
    for c in cases:
        results.append(c)
        f=out/'cases'/c['name']/'RESPONSE.csv'
        if f.exists():curves[c['name']]=read(f)
    parameters=[];comparison=[];tails=[];early=[]
    for c in results:
        m=c.get('metrics',{});d=c.get('diagnostic',{})
        parameters.append(dict(case=c['name'],source=c.get('source','NEW_RUN'),E_MPa=200000.,Fy_MPa=352.,Lp_mm=100.,mesh=c['mesh'],step_mm=c['step_mm'],**c['parameters'],**c['cyclic']))
        comparison.append(dict(case=c['name'],source=c.get('source','NEW_RUN'),valid=c['valid'],
          max_early_endpoint_change=d.get('max_early_endpoint_change_relative_to_P190'),early_preserved=d.get('early_preserved'),tail_pass=d.get('tail_retention_pass'),
          early_and_tail_goal=d.get('early_and_tail_goal'),inherited_checks=m.get('checks'),envelope_NRMSE=m.get('envelope_NRMSE'),shape_RMS=m.get('shape_RMS'),
          peak_relative_errors=m.get('peak_relative_errors'),tail_retention_errors=m.get('tail_retention_errors'),failure=c.get('failure')))
        key='ARCHIVED_P190_C060' if c.get('source')=='ARCHIVED_RUN_40202450657' else c['name']
        if key not in curves:continue
        end=endpoint_map(curves[key]);base_end=endpoint_map(baseline)
        for i in range(1,37):
            if i in end:early.append(dict(case=key,excursion=i,model_endpoint_kNm=float(end[i]['M_nominal_kNm']),
                P190_endpoint_kNm=float(base_end[i]['M_nominal_kNm']),ratio_to_P190=float(end[i]['M_nominal_kNm'])/float(base_end[i]['M_nominal_kNm']),
                scope='FIXED_RECONSTRUCTED_PREFIX_PRESERVATION_NOT_INDEPENDENT_CYCLE_ID_VALIDATION'))
        for sign,ids in [(1,list(range(57,66,2))),(-1,list(range(58,67,2)))]:
            first=abs(float(end[ids[0]]['M_nominal_kNm'])) if ids[0] in end else None
            for cycle,eid in enumerate(ids,1):
                if eid not in end:continue
                value=abs(float(end[eid]['M_nominal_kNm']));expvalue=abs(float(protocol[eid-1]['measured_moment_kNm']))
                tails.append(dict(case=key,cycle_in_final_block=cycle,sign=sign,excursion=eid,
                   model_abs_turning_moment_kNm=value,experiment_abs_turning_moment_kNm=expvalue,
                   model_ratio_to_first=value/first if first else None,
                   model_ratio_to_previous=value/abs(float(end[ids[cycle-2]]['M_nominal_kNm'])) if cycle>1 and ids[cycle-2] in end else None,
                   scope='TURNING_ENDPOINT_NOT_HALF_CYCLE_PEAK;CONDITIONAL_ON_FROZEN_PREHISTORY'))
    table(out/'PARAMETERS_BEFORE_AFTER.csv',parameters);table(out/'BEFORE_AFTER_METRICS.csv',comparison)
    table(out/'INITIAL_ENDPOINT_PRESERVATION.csv',early);table(out/'FINAL_FIVE_CYCLES.csv',tails)
    n=len(curves);fig,axes=plt.subplots(math_ceil(n/2),2,figsize=(12,4*math_ceil(n/2)),squeeze=False)
    for ax,(name,rows) in zip(axes.flat,curves.items()):
        ax.plot(et,em,color='.7',lw=.6,label='Fig15 vector geometry (not raw time)')
        ax.plot([float(r['theta_rad']) for r in rows],[float(r['M_nominal_kNm']) for r in rows],lw=.8,label=name)
        completed=max(int(r['excursion']) for r in rows)==66
        ax.set(xlabel='Total member rotation (rad)',ylabel='M=F*1372 (kNm)',title=name+('' if completed else ' [INCOMPLETE - not physical failure]'));ax.grid(alpha=.25);ax.legend(fontsize=7)
    for ax in list(axes.flat)[n:]:ax.axis('off')
    fig.tight_layout();fig.savefig(out/'HYSTERESIS_COMPARISON.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4.5))
    for ax,sign in zip(axes,[1,-1]):
        ids=list(range(57,66,2) if sign==1 else range(58,67,2))
        ax.plot(range(1,6),[abs(float(protocol[i-1]['measured_moment_kNm'])) for i in ids],'ko-',label='Experiment turning endpoints')
        for key,rows in curves.items():
            end=endpoint_map(rows);cycles=[j+1 for j,i in enumerate(ids) if i in end]
            ax.plot(cycles,[abs(float(end[ids[j-1]]['M_nominal_kNm'])) for j in cycles],'-o',label=key)
        ax.set(xlabel='Cycle within final maximum-amplitude block',ylabel='|M| at turning point (kNm)',title='Positive' if sign==1 else 'Negative');ax.set_xticks(range(1,6));ax.grid(alpha=.25);ax.legend(fontsize=7)
    fig.tight_layout();fig.savefig(out/'FINAL_FIVE_CYCLE_DEGRADATION.png',dpi=180);plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,4))
    for key in curves:
        if key=='ARCHIVED_P190_C060':continue
        q=[r for r in early if r['case']==key];ax.plot([r['excursion'] for r in q],[r['ratio_to_P190'] for r in q],label=key)
    tolerance=plan['early_preservation']['relative_endpoint_change_limit'];ax.axhline(1.,color='k',lw=.7);ax.axhline(1-tolerance,color='r',ls='--');ax.axhline(1+tolerance,color='r',ls='--')
    ax.set(xlabel='Reconstructed early excursion (uncertain physical cycle ID)',ylabel='Endpoint M / archived P190_C060');ax.grid(alpha=.25)
    if len(curves)>1:ax.legend(fontsize=7)
    fig.tight_layout();fig.savefig(out/'EARLY_RESISTANCE_PRESERVATION.png',dpi=180);plt.close(fig)
    # Retain the exact inherited envelope/cloud/rotation checks in the table;
    # a successful tail diagnostic cannot erase the old calibration failures.
def math_ceil(x):
    import math
    return math.ceil(x)
def run(binary_root,out):
    root=out/'inputs';verify(root);plan=load(root/'DAMAGE_SENSITIVITY_PLAN.json');cfg=load(root/'PREREGISTRATION.json')
    base=load(root/'baseline/CASE_RESULT.json');base_rows=read(root/'baseline/RESPONSE.csv');h=helper(root)
    ops,info=h.native_import(binary_root,cfg);dump(out/'NATIVE_PROVENANCE.json',info)
    sys.path.insert(0,str(root/'engine'));import validation_engine as v
    require(v.PARAMS==cfg['source_reference_parameters'],'N_OPT01 reference parameters changed')
    old_audit=h.model_audit;current=[None]
    def audit(model,ns,pts,config):
        a=old_audit(model,ns,pts,config);data=ops.eleResponse(101,'section',1,'fiber',0,'AllData')
        require(len(data)==48,'Unexpected material response layout')
        candidate=current[0];expected=[1.,1.,candidate['damage1'],candidate['damage2'],0.,-.5,-.5]
        require(all(abs(x-y)<1e-12 for x,y in zip(data[29:36],expected)),'Fixed pinching/beta or requested damage/degEnv not preserved')
        require(abs(data[38]-plan['scale_basis']['native_energyA_MPa'])<1e-8,'Initial native energyA normalization changed')
        require(data[1]==352. and abs(data[2]-.00176)<1e-12 and data[5]==668.8 and data[6]==.06,'Virgin P190_C060 changed')
        a['initial_native_energyA_MPa']=data[38];a['initial_native_damage_and_degenv']=data[31:36]
        return a
    h.model_audit=audit
    cases=[];skipped=[]
    for phase in ('D1','D2'):
        if phase=='D2' and any(c.get('diagnostic',{}).get('early_and_tail_goal') for c in cases):
            skipped=['D2_LOW','D2_HIGH'];break
        for candidate in [c for c in plan['candidates'] if c['name'].startswith(phase+'_')]:
            require(len(cases)<4,'Four-candidate budget exhausted');current[0]=candidate
            cyclic=dict(base['cyclic']);cyclic.update({k:candidate[k] for k in ('damage1','damage2','degEnvP','degEnvN')})
            print('DAMAGE_ONLY_CANDIDATE',candidate['name'],flush=True)
            case=h.run_case(v,root,out,cfg,candidate['name'],base['parameters'],cyclic,mesh=base['mesh'],step=base['step_mm'])
            require(case['parameters']==base['parameters'] and case['mesh']==base['mesh'] and case['step_mm']==base['step_mm'],'A non-damage model setting changed')
            if case['valid']:
                rows=h.curve(out,case['name']);case['diagnostic']=diagnostics(case,rows,base_rows,plan)
            case['source']='NEW_SAME_BINARY_RUN';cases.append(case);dump(out/'NEW_CANDIDATES.json',cases)
    eligible=[c for c in cases if c.get('diagnostic',{}).get('early_and_tail_goal')]
    complete=all(c['valid'] for c in cases)
    if eligible:
        global_ok=[c for c in eligible if c['metrics']['observational_pass']]
        decision='CONTINUE_SAME_MODEL_WITH_CONDITIONAL_SUPPORT' if global_ok else 'TAIL_ONLY_GLOBAL_MISFIT_REMAINS'
        matching=[c['name'] for c in eligible]
    else:
        decision='NO_ACCEPTABLE_TAIL_MATCH_IN_FROZEN_RANGE' if complete and len(cases)==4 else 'INCONCLUSIVE_NUMERICAL_OR_EXECUTION_FAILURE';matching=[]
    result=dict(decision=decision,new_candidate_count=len(cases),baseline_reruns=0,skipped_candidates=skipped,matching_candidates=matching,
       scope=plan['result_scope'],overall_calibration_pass_issued=False,
       explanation='A strength-only damage mechanism screen, not a new full calibration. Inherited criteria unchanged. No further candidates or parameter search started.')
    dump(out/'SENSITIVITY_RESULT.json',result);export(out,root,cases);verify(root)
def summarize(out):
    r=load(out/'SENSITIVITY_RESULT.json') if (out/'SENSITIVITY_RESULT.json').exists() else dict(decision='INCONCLUSIVE_NOT_EXECUTED_OR_INCOMPLETE',new_candidate_count=0,baseline_reruns=0)
    if (out/'EXECUTION_FAILURE.json').exists():r.update(decision='INCONCLUSIVE_NUMERICAL_OR_EXECUTION_FAILURE',failure=load(out/'EXECUTION_FAILURE.json'))
    dump(out/'RESULT.json',r)
    text='# حساسیت محدود damage در P190_C060\n\n**'+r['decision']+'**\n\n'
    text+=f"نامزدهای جدید اجراشده: {r.get('new_candidate_count',0)}، اجرای مجدد مرجع: ۰.\n\n"
    text+='E، Fy، هندسه، Lp=100، Backbone اولیه، مش، گام و پروتکل ثابت‌اند. فقط damage و degEnv مطابق جدول منجمد تغییر کرده‌اند. degEnv منفی محل کرنش نقاط را حفظ و فقط تنش نقاط ۲ به بعد را کاهش می‌دهد. این پارامترها خواص کوپن یا ضرایب تأییدشده Chao نیستند.\n\n'
    if (out/'BEFORE_AFTER_METRICS.csv').exists():
        text+='| حالت | حفظ پاسخ ابتدایی | افت بلوک نهایی | Envelope NRMSE |\n|---|---|---|---|\n'
        for a in read(out/'BEFORE_AFTER_METRICS.csv'):text+=f"|{a['case']}|{a['early_preserved']}|{a['tail_pass']}|{a['envelope_NRMSE']}|\n"
    text+='\nمعیار حفظ ابتدایی، تغییر حداکثر ۱۰٪ نقاط برگشت ۱–۳۶ نسبت به P190_C060 است؛ این همان مقیاس تلورانس مقاومت قبلی و یک کنترل اضافی است. هیچ معیار آزمایشگاهی قبلی آسان‌تر نشده است. معیار بلوک آخر، خطای مطلق حداکثر ۰٫۱۰ در نسبت نقاط برگشت چرخه‌های متوالیِ هر دو علامت است؛ این نقاط لزوماً اوج نیم‌چرخه نیستند.\n\n'
    text+='ترتیب کل تاریخچه PARTIAL است. تمام نتایج آسیب تجمعی به همین پیش‌تاریخ بازسازی‌شده مشروط‌اند؛ آسیب در شروع بلوک پایانی صفر نشده است. مرجع نمودارها همان اجرای کامل قبلی است و دوباره اجرا نشده است.\n\n'
    if r['decision']=='NO_ACCEPTABLE_TAIL_MATCH_IN_FROZEN_RANGE':text+='در چهار نامزد ازپیش‌تعیین‌شده، افت مناسب همراه حفظ پاسخ ابتدایی حاصل نشد. این محدودیت در دامنه بررسی‌شده است، نه اثبات عدم امکان برای همه مقادیر ماده. نامزد پنجم، تغییر معیار یا بهینه‌سازی تازه آغاز نمی‌شود.\n'
    elif r['decision']=='TAIL_ONLY_GLOBAL_MISFIT_REMAINS':text+='سازوکار، افت پایانی را همراه حفظ پاسخ ابتدایی بازتولید کرده، ولی معیارهای عمومی قبلی هنوز رد می‌شوند؛ CALIBRATION_PASS صادر نمی‌شود.\n'
    elif r['decision']=='CONTINUE_SAME_MODEL_WITH_CONDITIONAL_SUPPORT':text+='امکان ادامه محدود با همین مدل پشتیبانی می‌شود؛ این آزمایش به‌تنهایی اعتبارسنجی کامل یا قبولی کالیبراسیون نهایی نیست.\n'
    else:text+='اجرا یا کنترل عددی کامل نشده است؛ درباره قابلیت فیزیکی ماده نتیجه‌گیری نمی‌شود.\n'
    if r.get('failure'):text+='\nخطای واقعی: '+r['failure']['message']+'\n'
    (out/'REPORT_FA.md').write_text(text,encoding='utf-8')
    table(out/'OUTPUT_SHA256.csv',[dict(file=str(p.relative_to(out)),sha256=sha(p)) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='OUTPUT_SHA256.csv'])
    print(json.dumps(r,indent=2,ensure_ascii=False))
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('mode',choices=['prepare','run','summarize']);ap.add_argument('--payload',type=Path);ap.add_argument('--binary-root',type=Path);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    try:
        if a.mode=='prepare':prepare(a.payload,a.out)
        elif a.mode=='run':run(a.binary_root,a.out)
        else:summarize(a.out)
    except Exception as e:
        dump(a.out/'EXECUTION_FAILURE.json',dict(message=str(e),traceback=traceback.format_exc()));traceback.print_exc();return 1
    return 0
if __name__=='__main__':sys.exit(main())
