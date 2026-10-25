"""Finite, preregistered 2C200-1 candidate calibration; no building or STMF-1.
Upload to .github/scripts/limited_2c200.py. No pip OpenSees fallback or build.
All imported legacy model/solver files remain byte-identical to their payload.
"""
from pathlib import Path
import argparse,ast,copy,csv,hashlib,importlib.util,json,math,os,platform,sys,traceback,types,zipfile

PAYLOAD_SHA256='b1d39b5fe65698a625c5be7095d629744cc043a4fe59aa5fce96bec40d3def87'

def require(ok,message):
    if not ok:raise RuntimeError(message)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _json_standard_types(value):
    """Convert nested NumPy values without hiding unsupported/nonfinite data."""
    # Keep report-only modes usable even if dependency installation failed.
    try:
        import numpy as np
    except ImportError:
        np=None
    def convert(item):
        if np is not None:
            if isinstance(item,np.bool_):return bool(item)
            if isinstance(item,np.integer):return int(item)
            if isinstance(item,np.floating):return float(item)
            if isinstance(item,np.ndarray):return convert(item.tolist())
        if isinstance(item,dict):return {convert(k):convert(v) for k,v in item.items()}
        if isinstance(item,(list,tuple)):return [convert(v) for v in item]
        # json.dumps still rejects unsupported types; no string fallback.
        return item
    return convert(value)

def dump(p,value):p.write_text(json.dumps(_json_standard_types(value),indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def table(p,rows):
    if not rows:return
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader()
        w.writerows({k:json.dumps(v) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()} for r in rows)
def prepare(payload,out):
    require(sha(payload)==PAYLOAD_SHA256,'Frozen payload checksum changed')
    root=out/'inputs';root.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(payload) as z:
        for entry in z.infolist():
            target=(root/entry.filename).resolve()
            require(target.is_relative_to(root.resolve()),'Unsafe payload path')
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(entry))
    verify_payload(root)
    cfg=load(root/'PREREGISTRATION.json')
    require(cfg['budget']['max_calibration_evaluations_including_reference']==10,'Budget changed')
    require(cfg['E_MPa']==200000. and cfg['Fy_MPa']==352. and cfg['Lp_mm']==100.,'Physical inputs changed')
    dump(out/'FROZEN_EXECUTION_PLAN.json',cfg)
    dump(out/'PREPARED.json',dict(payload_sha256=PAYLOAD_SHA256,script_sha256=sha(__file__),
         input_count=len(load(root/'PAYLOAD_MANIFEST.json')),native_analysis_executed=False))

def verify_payload(root):
    for name,expected in load(root/'PAYLOAD_MANIFEST.json').items():
        require(sha(root/name)==expected,'Payload source/input changed: '+name)

def native_import(root,cfg):
    require(platform.system()=='Linux' and sys.version_info[:2]==(3,11),'Use tested Ubuntu/Python3.11 binary')
    found=list(root.rglob('opensees.so'));require(len(found)==1,'Exact passed binary not found uniquely')
    path=found[0].resolve();require(sha(path)==cfg['binary_sha256'],'Binary differs from passed gate')
    reports=[p for p in root.rglob('RESULT.json') if load(p).get('material_gate')=='PASS']
    require(len(reports)==1,'Passed material gate report missing/ambiguous')
    gate=load(reports[0]);require(gate.get('binary_gate')=='PASS' and gate.get('source_commit')==cfg['source_commit']
                               and gate.get('binary_sha256')==sha(path),'Material gate/binary/source mismatch')
    spec=importlib.util.spec_from_file_location('opensees',path);require(spec is not None and spec.loader is not None,'No native loader')
    ops=importlib.util.module_from_spec(spec);sys.modules['opensees']=ops;spec.loader.exec_module(ops)
    require(Path(ops.__file__).resolve()==path,'Wrong native import path')
    pkg=types.ModuleType('openseespy');pkg.__path__=[];pkg.opensees=ops
    sys.modules['openseespy']=pkg;sys.modules['openseespy.opensees']=ops
    return ops,dict(binary_path=str(path),binary_sha256=sha(path),source_commit=cfg['source_commit'],
                    version=ops.version(),python=sys.version,gate_report=str(reports[0]),gate_run=cfg['gate_run_id'])

def experiment(root):
    import numpy as np
    data=read(root/'engine/inputs/EXPERIMENT_DIGITIZATION.csv')
    protocol=read(root/'HALF_CYCLES_FINAL.csv')
    require(len(protocol)==66 and len(data)==7959,'Frozen experiment changed')
    return data,protocol,np.array([float(r['rotation_rad']) for r in data]),np.array([float(r['moment_kNm']) for r in data])

def envelope(theta,moment):
    import numpy as np
    values={}
    for sign in (1,-1):
        x=sign*theta;y=sign*moment
        for i in range(31):
            mask=(x>=i*.003)&(x<(i+1)*.003)&(y>=0)
            if mask.any():values[sign,i]=float(y[mask].max())
    return values

def cloud_distance(ta,ma,tb,mb,scale):
    import numpy as np
    # Equal occupied geometric cells, not PDF vertex density or inferred time.
    def points(t,m):
        a=np.unique(np.rint(np.column_stack([t/.0005,m/2.])).astype('int64'),axis=0).astype(float)
        return a*np.array([.0005/.091,2./scale])
    a,b=points(ta,ma),points(tb,mb)
    def nearest(p,q):
        result=[]
        for k in range(0,len(p),64):
            distance=((p[k:k+64,None,:]-q[None,:,:])**2).sum(axis=2)
            result.extend(distance.min(axis=1))
        return float(np.mean(result))
    return math.sqrt(.5*(nearest(a,b)+nearest(b,a)))

def end_ratios(rows):
    ends={}
    for r in rows:ends[int(r['excursion'])]=r
    values=[];endpoints=[]
    for sign,ids in [(1,range(57,66,2)),(-1,range(58,67,2))]:
        moments=[abs(float(ends[i]['M_nominal_kNm'])) for i in ids]
        require(all(v>1e-9 for v in moments),'Tail endpoint resistance zero or missing')
        values.extend([b/a for a,b in zip(moments,moments[1:])]);endpoints.append(moments)
    return values,endpoints

def assess(rows,status,root,cfg,physical):
    import numpy as np
    require(status['status']=='COMPLETED' and status['completed_excursions']==66,'Incomplete path is not a calibrated response')
    data,protocol,et,em=experiment(root)
    mt=np.array([float(r['theta_rad']) for r in rows]);mm=np.array([float(r['M_nominal_kNm']) for r in rows])
    require(np.isfinite(mt).all() and np.isfinite(mm).all(),'Nonfinite response')
    peak_errors=[];rotation_errors=[];observed=[]
    for sign in (1,-1):
        ie=int(np.argmax(sign*em));im=int(np.argmax(sign*mm));me=abs(float(em[ie]));mp=abs(float(mm[im]))
        pe=(mp-me)/me;bound=max(cfg['criteria']['observed_max_rotation_relative_error']*abs(et[ie]),2*cfg['criteria']['rotation_digitization_uncertainty_rad'])
        re=abs(abs(mt[im])-abs(et[ie]))/bound
        peak_errors.append(pe);rotation_errors.append(float(re))
        # Confirmation by a larger-deformation descending observation, not an
        # analysis failure or a small-amplitude reversal below previous peak.
        confirmed=bool(np.any((sign*mt>abs(mt[im])+2*cfg['criteria']['rotation_digitization_uncertainty_rad'])&(sign*mm>0)&(sign*mm<.95*mp)))
        observed.append(dict(sign=sign,experimental_max_kNm=me,model_observed_max_kNm=mp,
           experimental_max_rotation_rad=float(et[ie]),model_observed_max_rotation_rad=float(mt[im]),
           peak_confirmed_by_larger_deformation_decline=confirmed,claim='OBSERVED_MAXIMUM_NOT_AUTOMATICALLY_TRUE_BACKBONE_PEAK'))
    ee,me=envelope(et,em),envelope(mt,mm);common=sorted(set(ee)&set(me))
    coverage=len(common)/len(ee);require(coverage>=.8,'Insufficient common populated envelope bins')
    norm=float(max(abs(em)));env_rms=math.sqrt(float(np.mean([(me[k]-ee[k])**2 for k in common])))/norm
    a=int(protocol[0]['vector_sequence_index']);mask=(et[:a]>.0005)&(et[:a]<.0025)
    require(mask.sum()>=3,'Insufficient published elastic branch points')
    ke=float(np.dot(et[:a][mask],em[:a][mask])/np.dot(et[:a][mask],et[:a][mask]))
    modelmask=np.array([int(r['excursion'])==1 for r in rows])&(mt>.0005)&(mt<.0025)
    require(modelmask.sum()>=3,'Insufficient model stiffness fit points')
    km=float(np.dot(mt[modelmask],mm[modelmask])/np.dot(mt[modelmask],mt[modelmask]));ki_error=km/ke-1
    shape=cloud_distance(et,em,mt,mm,norm)
    reference=[dict(excursion=int(r['excursion']),M_nominal_kNm=float(r['measured_moment_kNm'])) for r in protocol]
    rt,endpoint_exp=end_ratios(reference);rm,endpoint_model=end_ratios(rows);tail_error=[m-e for m,e in zip(rm,rt)]
    c=cfg['criteria'];j1=.4*(env_rms/c['envelope_NRMSE'])**2+.3*float(np.mean((np.array(peak_errors)/c['signed_peak_relative_error'])**2))+.2*float(np.mean(np.array(rotation_errors)**2))+.1*(ki_error/c['stiffness_relative_error'])**2
    j2=.6*j1+.2*(shape/c['order_free_cloud_RMS'])**2+.2*float(np.mean((np.array(tail_error)/c['tail_endpoint_retention_abs_error'])**2))
    envelope_pass=abs(ki_error)<=c['stiffness_relative_error'] and max(abs(v) for v in peak_errors)<=c['signed_peak_relative_error'] and max(rotation_errors)<=1 and env_rms<=c['envelope_NRMSE']
    checks=dict(stiffness=abs(ki_error)<=c['stiffness_relative_error'],signed_observed_maxima=max(abs(v) for v in peak_errors)<=c['signed_peak_relative_error'],
       observed_max_rotation=max(rotation_errors)<=1,envelope_NRMSE=env_rms<=c['envelope_NRMSE'],
       order_free_shape=shape<=c['order_free_cloud_RMS'],conditional_tail_ratios=max(abs(v) for v in tail_error)<=c['tail_endpoint_retention_abs_error'],
       numerical_work=physical['max_work_error_ratio']<=c['numerical_energy_work_ratio'],
       envelope_strain_domain=physical['max_abs_strain']<=.8,all_inherited_screens_and_restore=status['all_restore_checks_passed'])
    tail_events={}
    for sign in (1,-1):
        im=int(np.argmax(sign*mm));peak=abs(mm[im]);cross=None
        for r in rows:
            eid=int(r['excursion']);index=list(range(57,66,2) if sign==1 else range(58,67,2))
            if eid in index and abs(float(r['theta_rad'])-float(protocol[eid-1]['rotation_rad']))<1e-7 and int(r['step'])>int(rows[im]['step']) and sign*float(r['M_nominal_kNm'])<=.8*peak:
                cross=dict(excursion=eid,cycle_within_maximum_block=(eid-57)//2+1,theta_rad=float(r['theta_rad']));break
        tail_events[str(sign)]=dict(first_accepted_tail_endpoint_below_80pct_observed_max=cross,status='OBSERVED' if cross else 'NOT_REACHED_CENSORED')
    return dict(J1=j1,J2=j2,envelope_stage_pass=envelope_pass,observational_pass=all(checks.values()),checks=checks,
       envelope_NRMSE=env_rms,envelope_bin_coverage=coverage,shape_RMS=shape,peak_relative_errors=peak_errors,
       observed_max_rotation_error_ratio=rotation_errors,initial_stiffness_model=km,initial_stiffness_published=ke,stiffness_relative_error=ki_error,
       tail_retention_errors=tail_error,tail_endpoint_model_kNm=endpoint_model,tail_endpoint_experiment_kNm=endpoint_exp,
       tail_retention_scope='Conditional only on supported maximum-amplitude block and fixed uncertain prehistory',
       observed_maxima=observed,tail_80pct_observed_max=tail_events,last_accepted_rotation=float(mt[-1]),
       nominal_Mp_kNm=345.*2*227780.1896/1e6,
       experimental_cycle_energy='UNVERIFIED_EXCLUDED_FROM_OBJECTIVE_AND_PASS',experimental_total_cumulative_damage='UNVERIFIED_EXCLUDED',
       physical=physical)

def install_instrumentation(v,out,params,cyclic,points):
    import numpy as np
    core=v.engines()['v09_core'];native=v.engines()['postpeak_solver']
    old_material=v.material;old_monitor=core.Monitor;old_state=native.native_state;old_compare=native.compare
    v.PARAMS=copy.deepcopy(params)
    flags=['-pinch',cyclic['pinchX'],cyclic['pinchY'],'-beta',cyclic['beta'],'-damage',cyclic['damage1'],cyclic['damage2'],'-degEnv',cyclic['degEnvP'],cyclic['degEnvN']]
    commands=[]
    class Proxy:
        def __init__(self,ops):self.ops=ops
        def __getattr__(self,n):return getattr(self.ops,n)
        def uniaxialMaterial(self,*a):
            a=tuple(a)+tuple(flags) if a[0]=='HystereticSM' else a;commands.append(list(a));return self.ops.uniaxialMaterial(*a)
    def material(ops,tag,fy,E,lp):return old_material(Proxy(ops),tag,fy,E,lp)
    v.material=material
    # Index-based fiber requests retain separate channel identities even though
    # the 2D section cannot represent differential out-of-plane channel failure.
    indices=[]
    for channel in (-1,1):
        ids=[i for i,p in enumerate(points) if p['channel']==channel]
        indices.extend([min(ids,key=lambda i:points[i]['y_mm']),max(ids,key=lambda i:points[i]['y_mm'])])
    def extended_state(ops,ns):
        x=old_state(ops,ns)
        for ip in (1,6):
            for fi in indices:
                a=np.array(ops.eleResponse(101,'section',ip,'fiber',fi,'AllData'),float)
                require(a.shape==(48,),'Unknown native material state in actual forceBeamColumn')
                x[('material',101,ip,fi)]=a
        return x
    def compare(a,b):
        ok,err=old_compare(a,b)
        for k in a:
            if k[0]=='material':
                x,y=a[k],b[k]
                limits=1e-9+1e-9*np.maximum(abs(x),abs(y));limits[1:29]=1e-8
                ratio=float(max(abs(x-y)/limits));err=max(err,ratio);ok=ok and ratio<=1
        return ok,err
    native.native_state=extended_state;native.compare=compare
    physical=dict(max_abs_strain=0.,max_actual_strain_increment=0.,max_abs_section_axial_N=0.,max_work_error_ratio=0.,
      first_reference_cap_crossing=None,first_sampled_native_softening=None,first_yield=None,
      exact_all_fiber_softening_onset='UNVERIFIED; native negative tangent sampled at four fibers per nonlinear IP',
      initial_axial_load_N=0.,experimental_axial_history='UNVERIFIED',
      absolute_Lp='UNVERIFIED_FIXED_100MM',actual_Fu='UNVERIFIED',weld_fracture_state='NOT_EXPLICITLY_REPRESENTED')
    previous={};work=[0.,0.];last=[0.,0.]
    class Monitor(old_monitor):
        def capture(self,ns,step,roof,shear,additional_reasons=None):
            super().capture(ns,step,roof,shear,additional_reasons)
            ops=ns['ops'];force=float(ops.getLoadFactor(30));du=roof-last[0];dw=.5*(force+last[1])*du
            work[0]+=dw;work[1]+=abs(dw);last[:]=[roof,force]
            er=abs(self.work-work[0])/max(work[1],1.)
            physical['max_work_error_ratio']=max(physical['max_work_error_ratio'],er)
            critical=[];maxeps=0.;maxaxial=0.;softarea=0.;totalarea=0.
            for ip in (1,6):
                data=np.array(ops.eleResponse(101,'section',ip,'fiberData'),float).reshape(-1,5)
                eps=data[:,4];maxeps=max(maxeps,float(max(abs(eps))))
                sf=ops.eleResponse(101,'section',ip,'force');maxaxial=max(maxaxial,abs(float(sf[0])))
                if ip in previous:physical['max_actual_strain_increment']=max(physical['max_actual_strain_increment'],float(max(abs(eps-previous[ip]))))
                previous[ip]=eps.copy();totalarea+=float(sum(data[:,2]));softarea+=float(sum(data[abs(eps)>=params['cap'],2]))
                for fi in indices:
                    a=ops.eleResponse(101,'section',ip,'fiber',fi,'AllData')
                    if a[47]<0 and physical['first_sampled_native_softening'] is None:
                        physical['first_sampled_native_softening']=dict(step=step,theta_rad=roof/1372.,ip=ip,fiber=fi,strain=float(eps[fi]),tangent_MPa=a[47])
                    critical.append(dict(step=step,u_mm=roof,ip=ip,fiber=fi,channel=points[fi]['channel'],zone=points[fi]['zone'],
                       y_mm=points[fi]['y_mm'],area_mm2=points[fi]['area_mm2'],stress_MPa=float(data[fi,3]),strain=float(eps[fi]),
                       tangent_MPa=a[47],native_positive_cap_strain=a[6],native_negative_cap_strain=a[20],
                       committed_energy_density_MPa=a[43],degEnvP=a[34],degEnvN=a[35]))
            physical['max_abs_strain']=max(physical['max_abs_strain'],maxeps);physical['max_abs_section_axial_N']=max(physical['max_abs_section_axial_N'],maxaxial)
            if physical['first_yield'] is None and maxeps>=352./200000.:physical['first_yield']=dict(step=step,theta_rad=roof/1372.,M_kNm=force*1372/1e6,max_abs_strain=maxeps)
            if physical['first_reference_cap_crossing'] is None and maxeps>=params['cap']:physical['first_reference_cap_crossing']=dict(step=step,theta_rad=roof/1372.,max_abs_strain=maxeps)
            core.append(out/'CRITICAL_FIBERS.csv',critical)
            core.append(out/'PHYSICAL_HISTORY.csv',[dict(step=step,u_mm=roof,force_N=force,max_abs_fiber_strain=maxeps,max_abs_section_axial_N=maxaxial,
                fraction_nonlinear_IP_fiber_area_at_or_past_reference_cap=softarea/totalarea,
                root_moment_Nmm=ops.nodeReaction(1,3),nominal_moment_Nmm=force*1372.,tip_axial_displacement_mm=ops.nodeDisp(2,2),
                internal_work_Nmm=self.work,external_work_Nmm=work[0],absolute_external_work_Nmm=work[1],work_error_ratio=er)])
    core.Monitor=Monitor
    def restore():
        v.material=old_material;core.Monitor=old_monitor;native.native_state=old_state;native.compare=old_compare
    return physical,commands,restore

def model_audit(v,ns,pts,cfg):
    ops=ns['ops'];props=v.section_properties(pts);reference=dict(A_mm2=2*3555.,I_strong_mm4=2*18272560.,Z_strong_mm3=2*227780.1896)
    errors={k:props[k]/value-1 for k,value in reference.items()}
    require(max(abs(x) for x in errors.values())<=.005,'Double-channel nominal section mismatch')
    require(set(p['Fy_MPa'] for p in pts)=={352.} and len(pts) in (64,128),'Wrong material/mesh')
    require(set(p['channel'] for p in pts)=={-1,1},'Not two explicit channels')
    require(tuple(ops.nodeCoord(1))==(0.,0.) and tuple(ops.nodeCoord(2))==(0.,1372.),'Wrong specimen moment arm')
    fixed=[tuple(a) for name,a,k in ops.v09_outside_commands if name=='fix'];require((1,1,1,1) in fixed,'Wrong root boundary')
    weights=ops.eleResponse(101,'integrationWeights');require(abs(sum(weights)-1372.)<1e-7,'Integration weights do not sum to member length')
    return dict(properties=props,nominal_property_relative_errors=errors,channels=2,Fy_MPa=352.,E_MPa=200000.,Lp_mm=100.,
       root='Existing fixed weld end; no new root spring',moment='F_N*1372/1e6 kNm',rotation='u_tip_mm/1372 total rotation',
       moment_work_conjugacy='M_nominal dtheta = Fdu',initial_axial_load_N=0.,
       integration_points=ops.eleResponse(101,'integrationPoints'),integration_weights=weights,
       elastic_IPs=[2,3,4,5],model_limitations=cfg['physics_scope'])

def run_case(v,root,out,cfg,name,params,cyclic,mesh=64,step=1.,monotonic=False):
    directory=out/'cases'/name;directory.mkdir(parents=True)
    pts=v.channel_fibers(mesh);physical,commands,restore=install_instrumentation(v,directory,params,cyclic,pts)
    try:
        ns,pts=v.cantilever(mesh=mesh,lp=100.,E=200000.,root_ratio=None,elastic=False)
        dump(directory/'INPUT_AUDIT.json',model_audit(v,ns,pts,cfg));table(directory/'SECTION_FIBERS.csv',pts)
        protocol=read(root/'HALF_CYCLES_FINAL.csv')
        targets=[1372*.18] if monotonic else [float(r['tip_displacement_mm']) for r in protocol]
        rows,status=v.run_path(ns,directory,targets,step_mm=step,full_fibers=False)
        metrics=assess(rows,status,root,cfg,physical) if not monotonic else dict(kind='MONOTONIC_PREDICTION_NOT_EXPERIMENTAL_VALIDATION',physical=physical)
        numerical_ok=physical['max_work_error_ratio']<=cfg['criteria']['numerical_energy_work_ratio'] and physical['max_abs_strain']<=.8 and status['all_restore_checks_passed']
        result=dict(name=name,valid=status['status']=='COMPLETED' and numerical_ok,parameters=params,cyclic=cyclic,mesh=mesh,step_mm=step,
                    metrics=metrics,status=status,monotonic=monotonic)
        if not numerical_ok:result['failure']='Numerical work/domain/restore gate failed; not physical deterioration'
    except Exception as e:
        result=dict(name=name,valid=False,parameters=params,cyclic=cyclic,mesh=mesh,step_mm=step,monotonic=monotonic,
                    failure=str(e),traceback=traceback.format_exc(),physical=physical)
        dump(directory/'CASE_FAILURE.json',result)
    finally:
        restore();v.raw.wipe();dump(directory/'ACTUAL_MATERIAL_COMMANDS.json',commands)
    dump(directory/'CASE_RESULT.json',result)
    return result

def curve(out,name):return read(out/'cases'/name/'RESPONSE.csv')
def sensitivity(a,b,cfg):
    import numpy as np
    def arrays(rows):
        t=np.array([float(r['theta_rad']) for r in rows]);m=np.array([float(r['M_nominal_kNm']) for r in rows]);p=np.r_[0.,np.cumsum(abs(np.diff(t)))];mask=np.r_[True,np.diff(p)>1e-12];return p[mask],m[mask]
    x,y=arrays(a);xx,yy=arrays(b);require(abs(x[-1]-xx[-1])<1e-6,'Different completed sensitivity histories')
    grid=np.linspace(0,min(x[-1],xx[-1]),4001);rms=float(np.sqrt(np.mean((np.interp(grid,x,y)-np.interp(grid,xx,yy))**2))/max(abs(y)))
    peak=max(abs(float(max(yy))-float(max(y)))/max(abs(max(y)),1e-12),abs(float(min(yy))-float(min(y)))/max(abs(min(y)),1e-12))
    ra,_=end_ratios(a);rb,_=end_ratios(b);tail=max(abs(v-w) for v,w in zip(ra,rb));c=cfg['criteria']
    return dict(curve_NRMSE=rms,max_signed_peak_relative_difference=peak,max_tail_ratio_difference=tail,
       pass_check=rms<=c['sensitivity_curve_NRMSE'] and peak<=c['sensitivity_signed_peak_relative_error'] and tail<=c['sensitivity_tail_ratio_abs_difference'])

def plots_and_tables(out,root,cases,winner,monotonic):
    import numpy as np
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _,_,et,em=experiment(root)
    fig,axes=plt.subplots(1,2,figsize=(12,5))
    axes[0].plot(et,em,color='.7',lw=.6,label='Published Fig15 vectors (geometry; not raw time)')
    ee=envelope(et,em)
    for sign in (1,-1):
        keys=sorted(k for k in ee if k[0]==sign);axes[1].plot([sign*(k[1]+.5)*.003 for k in keys],[sign*ee[k] for k in keys],color='.4',ls='--',label='Published geometric envelope' if sign==1 else None)
    for name,label in [('REFERENCE','Uncalibrated new-binary reference'),(winner,'Selected bounded candidate')]:
        if not name or not (out/'cases'/name/'RESPONSE.csv').exists():continue
        rows=curve(out,name);t=np.array([float(r['theta_rad']) for r in rows]);m=np.array([float(r['M_nominal_kNm']) for r in rows])
        axes[0].plot(t,m,lw=.8,label=label);e=envelope(t,m)
        for sign in (1,-1):
            keys=sorted(k for k in e if k[0]==sign);axes[1].plot([sign*(k[1]+.5)*.003 for k in keys],[sign*e[k] for k in keys],label=label if sign==1 else None)
    for a in axes:a.set(xlabel='Total member rotation u/1372 (rad)',ylabel='Weld-reference M=F*1372 (kNm)');a.grid(alpha=.25);a.legend(fontsize=7)
    fig.tight_layout();fig.savefig(out/'EXPERIMENT_REFERENCE_CANDIDATE.png',dpi=180);plt.close(fig)
    if monotonic:
        fig,ax=plt.subplots(figsize=(7,5))
        for name in monotonic:
            if (out/'cases'/name/'RESPONSE.csv').exists():
                rows=curve(out,name);ax.plot([float(r['theta_rad']) for r in rows],[float(r['M_nominal_kNm']) for r in rows],label=name)
        ax.set(xlabel='Total member rotation (rad)',ylabel='Moment (kNm)',title='Monotonic predictions; no independent monotonic 2C200-1 data');ax.grid(alpha=.25);ax.legend();fig.tight_layout();fig.savefig(out/'MONOTONIC_PREDICTIONS_UNVERIFIED.png',dpi=180);plt.close(fig)
    metric_rows=[];parameter_rows=[]
    for r in cases:
        parameter_rows.append(dict(case=r['name'],selected=r['name']==winner,role='REFERENCE' if r['name']=='REFERENCE' else 'INDEPENDENT_CANDIDATE',E_MPa=200000.,Fy_MPa=352.,Lp_mm=100.,mesh=r['mesh'],step_mm=r['step_mm'],**r['parameters'],**r['cyclic']))
        m=r.get('metrics',{});metric_rows.append(dict(case=r['name'],valid=r['valid'],J1=m.get('J1'),J2=m.get('J2'),
           envelope_NRMSE=m.get('envelope_NRMSE'),geometric_shape_RMS=m.get('shape_RMS'),signed_peak_relative_errors=m.get('peak_relative_errors'),
           observed_max_rotation_error_ratio=m.get('observed_max_rotation_error_ratio'),stiffness_relative_error=m.get('stiffness_relative_error'),
           conditional_tail_ratio_errors=m.get('tail_retention_errors'),observational_pass=m.get('observational_pass'),failure=r.get('failure')))
    table(out/'PARAMETERS_BEFORE_AFTER_ALL_EVALUATIONS.csv',parameter_rows);table(out/'ERROR_TABLE.csv',metric_rows)

def run(payload_root,binary_root,out):
    import numpy as np
    require(np.__version__=='1.26.4','Match registered NumPy')
    cfg=load(payload_root/'PREREGISTRATION.json');verify_payload(payload_root)
    ops,info=native_import(binary_root,cfg);dump(out/'NATIVE_PROVENANCE.json',info)
    sys.path.insert(0,str(payload_root/'engine'));import validation_engine as v
    require(v.PARAMS==cfg['source_reference_parameters'],'Reference N_OPT01 changed')
    base=copy.deepcopy(v.PARAMS);cyclic0=cfg['reference_cyclic'];cases=[];monos=[];sens=[]
    # Reuse the exact elastic-check function from the old real validator,
    # never its main (which launches an unrelated sensitivity campaign).
    tree=ast.parse((payload_root/'legacy_reference/validate_2C200_1.py.txt').read_text(encoding='utf-8-sig'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='elastic_check')
    ns={'v':v};exec(compile(ast.Module(body=[fn],type_ignores=[]),'<original elastic check>','exec'),ns)
    dump(out/'ELASTIC_MODEL_PREFLIGHT.json',[ns['elastic_check'](n) for n in (64,128)])
    def evaluate(name,p,cyc):
        require(len(cases)<10,'Evaluation budget exhausted')
        print('EVALUATION',len(cases)+1,name,flush=True)
        r=run_case(v,payload_root,out,cfg,name,p,cyc);cases.append(r);dump(out/'EVALUATIONS.json',cases);return r
    reference=evaluate('REFERENCE',base,cyclic0)
    if not reference['valid']:
        outcome=dict(decision='INCONCLUSIVE',reason='Corrected-binary reference member path failed; no physical calibration inferred',winner=None)
        dump(out/'CALIBRATION_RESULT.json',outcome);plots_and_tables(out,payload_root,cases,None,[]);return
    # Uniform prediction diagnostic for reference, not a calibration evaluation.
    mono=run_case(v,payload_root,out,cfg,'REFERENCE_MONOTONIC',base,cyclic0,step=.5,monotonic=True);monos.append(mono['name'])
    for change in cfg['stage1_candidates']:
        p=copy.deepcopy(base);p.update(peak_ratio=change['peak_ratio'],cap=change['cap']);evaluate(change['name'],p,cyclic0)
    eligible=[r for r in cases if r['valid'] and r['metrics']['envelope_stage_pass']]
    if not eligible:
        valid=[r for r in cases if r['valid']];closest=min(valid,key=lambda r:(r['metrics']['J1'],r['name']))
        altered_valid=any(r['valid'] for r in cases if r['name']!='REFERENCE')
        outcome=dict(decision='CALIBRATION_BLOCK' if altered_valid else 'INCONCLUSIVE',reason='No preregistered backbone meets initial envelope/strength/rotation/stiffness criteria; cyclic stage not started' if altered_valid else 'All altered backbone paths failed numerically; not evidence of physical strength loss',winner=closest['name'],verification='UNVERIFIED_SKIPPED_AFTER_FAILED_STAGE1')
    else:
        backbone=min(eligible,key=lambda r:(r['metrics']['J1'],r['name']))
        for option in cfg['stage2_options']:
            candidate={k:v for k,v in option.items() if k!='name'};evaluate(backbone['name']+'_'+option['name'],backbone['parameters'],candidate)
        valid=[r for r in cases if r['valid']];closest=min(valid,key=lambda r:(r['metrics']['J2'],r['name']))
        passed=[r for r in valid if r['metrics']['observational_pass']]
        if not passed:
            outcome=dict(decision='CALIBRATION_BLOCK',reason='Finite candidate budget exhausted without all permitted observational and numerical criteria',winner=closest['name'],verification='UNVERIFIED_SKIPPED_AFTER_OBSERVATIONAL_FAILURE')
        else:
            selected=min(passed,key=lambda r:(r['metrics']['J2'],r['name']));closest=selected
            for name,mesh,step in [('VERIFY_MESH128',128,1.),('VERIFY_HALF_STEP',64,.5)]:
                check=run_case(v,payload_root,out,cfg,name,selected['parameters'],selected['cyclic'],mesh=mesh,step=step)
                s=dict(case=name,valid=check['valid'])
                if check['valid']:s.update(sensitivity(curve(out,selected['name']),curve(out,name),cfg));s['member_numerical_checks']=check['metrics']['checks']
                else:s.update(pass_check=False,failure=check.get('failure'))
                sens.append(s)
            verification_ok=all(s['valid'] and s['pass_check'] and all(s['member_numerical_checks'].values()) for s in sens)
            outcome=dict(decision='CALIBRATION_PASS' if verification_ok else 'CALIBRATION_BLOCK',reason='Limited permitted-data calibration and numerical verification passed' if verification_ok else 'Numerical verification did not pass; no additional optimization',winner=selected['name'])
            mono=run_case(v,payload_root,out,cfg,'SELECTED_MONOTONIC',selected['parameters'],selected['cyclic'],step=.5,monotonic=True);monos.append(mono['name'])
            if not mono['valid'] or mono['metrics']['physical']['max_work_error_ratio']>cfg['criteria']['numerical_energy_work_ratio']:
                outcome.update(decision='CALIBRATION_BLOCK',reason='Selected monotonic prediction failed numerical/energy control; no extrapolation freeze')
            if outcome['decision']=='CALIBRATION_PASS':
                dump(out/'FROZEN_CALIBRATED_CANDIDATE.json',dict(parameters=selected['parameters'],cyclic=selected['cyclic'],transfer_rule=cfg['transfer_rule'],
                  scope='LIMITED_PUBLISHED_DATA_CALIBRATION_CONDITIONAL_ON_FIXED_UNVERIFIED_PREHISTORY',physical_validation=False,
                  source_commit=cfg['source_commit'],binary_sha256=cfg['binary_sha256'],next_stage_only='INDEPENDENT_STMF_1'))
    outcome.update(calibration_evaluations=len(cases),max_allowed=10,scope='LIMITED_CONDITIONAL_CALIBRATION; NOT FULL_DAMAGE_LIFE_VALIDATION',
       unknowns=cfg['physics_scope'],experiment_global_cycle_order='PARTIAL_FINAL; NOT_REAUDITED',sensitivities=sens,
       uniform_response='Prediction only; no independent monotonic laboratory record',next_stage_only='INDEPENDENT_STMF_1' if outcome['decision']=='CALIBRATION_PASS' else 'Limited model/parameter-range review only; no automatic rerun')
    m=closest['metrics'];c=cfg['criteria']
    relative=dict(stiffness=abs(m['stiffness_relative_error'])/c['stiffness_relative_error'],
       signed_peak=max(abs(x) for x in m['peak_relative_errors'])/c['signed_peak_relative_error'],
       envelope=m['envelope_NRMSE']/c['envelope_NRMSE'],rotation=max(m['observed_max_rotation_error_ratio']),
       shape=m['shape_RMS']/c['order_free_cloud_RMS'],tail_retention=max(abs(x) for x in m['tail_retention_errors'])/c['tail_endpoint_retention_abs_error'],
       numerical_work=m['physical']['max_work_error_ratio']/c['numerical_energy_work_ratio'])
    outcome['ranked_error_to_tolerance']=[dict(metric=k,error_over_tolerance=v) for k,v in sorted(relative.items(),key=lambda kv:-kv[1])]
    outcome['closest_identified_limit']=outcome['ranked_error_to_tolerance'][0]
    outcome['failed_candidate_count']=sum(not r['valid'] for r in cases)
    dump(out/'SENSITIVITY.json',sens);dump(out/'CALIBRATION_RESULT.json',outcome)
    plots_and_tables(out,payload_root,cases,outcome['winner'],monos)
    v.PARAMS=copy.deepcopy(base)
    require(v.PARAMS==cfg['source_reference_parameters'],'Reference parameter restore failed')
    verify_payload(payload_root)

def summarize(out):
    result=load(out/'CALIBRATION_RESULT.json') if (out/'CALIBRATION_RESULT.json').exists() else dict(decision='INCONCLUSIVE',reason='GitHub member analyses have not completed',winner=None,calibration_evaluations=0)
    failure=load(out/'EXECUTION_FAILURE.json') if (out/'EXECUTION_FAILURE.json').exists() else None
    if failure:result.update(decision='INCONCLUSIVE',reason=failure['message'])
    dump(out/'RESULT.json',result)
    report=f"# کالیبراسیون محدود 2C200-1\n\n**{result['decision']}**\n\n"
    report+='باینری اصلاح‌شده همان Artifact آزمون PASS است؛ OpenSees ساخته یا نصب مجدد نشده است. E=200000، Fy=352 MPa، هندسه دو ناودانی و Lp=100 mm ثابت‌اند. نامزدها در حافظه مستقل ساخته می‌شوند؛ مرجع N_OPT01 و مدل‌های اصلی دست‌نخورده‌اند.\n\n'
    report+=f"ارزیابی‌های کالیبراسیون انجام‌شده: {result.get('calibration_evaluations',0)} از سقف ۱۰. نامزد منتخب/نزدیک‌ترین: `{result.get('winner')}`.\n\n"
    report+='علت/نتیجه اجرایی: '+result.get('reason','')+'\n\n'
    if result.get('ranked_error_to_tolerance'):
        report+='بزرگ‌ترین نسبت‌های خطا به تلورانس (بیش از ۱ یعنی رد معیار): '+ '؛ '.join(f"{r['metric']}={r['error_over_tolerance']:.3g}" for r in result['ranked_error_to_tolerance'][:3])+'.\n\n'
    report+='ترتیب پروتکل PARTIAL باقی می‌ماند. پوش و شکل هندسی حلقه‌ها بدون اتکا به شماره تمام چرخه‌ها سنجیده شده‌اند؛ افت تکراری فقط در پنج چرخه پایانی و با پیش‌تاریخ ثابتِ بازسازی‌شده ارزیابی می‌شود. ضرایب آسیب خارج از این سناریو یکتا یا اعتبارسنجی‌شده معرفی نمی‌شوند. انرژی تجمعی کل آزمایش و انرژی چرخه‌های با ترتیب نامطمئن معیار PASS نیستند.\n\n'
    if (out/'ERROR_TABLE.csv').exists():
        rows=read(out/'ERROR_TABLE.csv');focus=[r for r in rows if r['case'] in ('REFERENCE',result.get('winner'))]
        report+='| مدل | Envelope NRMSE | فاصله هندسی شکل | خطای نسبی اوج‌های علامت‌دار |\n|---|---:|---:|---|\n'
        for r in focus:report+=f"| {r['case']} | {r['envelope_NRMSE']} | {r['geometric_shape_RMS']} | {r['signed_peak_relative_errors']} |\n"
        report+='\nمقادیر «بیشینه مشاهده‌شده» و محل آن از «اوج واقعی تأییدشده» جدا ثبت شده‌اند. توقف/گام ردشده افت مقاومت تلقی نشده است. جدول کامل خطا، پارامترها، کنترل‌ها و تاریخچه‌ها در Artifact همراه است.\n\n'
    report+='کنترل‌های جوش/تماس/شکست موضعی، Fu واقعی، انرژی شکست فیزیکی و اعتبار مطلق Lp برابر UNVERIFIED هستند. مدل یکنواخت صرفاً پیش‌بینی است؛ داده مستقل یکنواخت 2C200-1 موجود نیست.\n\n'
    if result['decision']=='CALIBRATION_PASS':report+='پارامترها و قانون انتقال در FROZEN_CALIBRATED_CANDIDATE.json منجمد شدند. تنها مرحله بعد اعتبارسنجی مستقل STMF-1 است؛ این نتیجه مجوز انتقال مستقیم به ساختمان نیست.\n'
    else:report+='هیچ دور کالیبراسیون اضافی آغاز نمی‌شود. اگر معیارهای مقاومت/Envelope غالب‌اند، محدودیت Backbone مؤثر بررسی شود؛ اگر افت نامتقارن چرخه پنجم غالب است، نبود مکانیزم شکست موضعی یک توضیح احتمالی است، نه علت اثبات‌شده. در شکست عددی نیز نتیجه فیزیکی استنتاج نشود.\n'
    (out/'REPORT_FA.md').write_text(report,encoding='utf-8')
    table(out/'OUTPUT_SHA256.csv',[dict(file=str(p.relative_to(out)),sha256=sha(p)) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='OUTPUT_SHA256.csv'])
    print(json.dumps(result,indent=2,ensure_ascii=False))

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('mode',choices=['prepare','run','summarize']);ap.add_argument('--payload',type=Path);ap.add_argument('--binary-root',type=Path);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    try:
        if a.mode=='prepare':prepare(a.payload,a.out)
        elif a.mode=='run':run(a.out/'inputs',a.binary_root,a.out)
        else:summarize(a.out)
    except Exception as e:
        dump(a.out/'EXECUTION_FAILURE.json',dict(mode=a.mode,message=str(e),traceback=traceback.format_exc()));traceback.print_exc();return 1
    return 0
if __name__=='__main__':sys.exit(main())
