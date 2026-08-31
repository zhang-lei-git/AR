(() => {
  'use strict';

  const ROLE_KEY = 'ar-management-role-v3';
  const TASK_KEY = 'ar-work-order-v3';
  const roleNames = {engineer:'工艺工程师', expert:'远程专家', admin:'系统管理员'};
  const capabilityNames = {display:'图文/视频/三维显示', camera:'第一视角采集', audio:'双向音频', voice:'语音输入', button:'按键输入'};
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const text = (id, value) => { const el = $(id); if (el) el.textContent = value; };
  const fmtTime = value => value ? new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) : '--';
  const statusInfo = status => ({running:['执行中','badge-running'],paused:['异常暂停','badge-danger'],pending:['待领取','badge-neutral'],completed:['已完工','badge-success'],published:['已发布','badge-success'],archived:['已归档','badge-neutral'],draft:['草稿','badge-running']}[status] || [status,'badge-neutral']);
  const eventLabel = type => ({WORK_ORDER_CREATED:'创建',WORK_ORDER_DISPATCHED:'下发',WORK_ORDER_CLAIMED:'领取',PART_IDENTIFIED:'匹配',PART_REVIEW_REQUIRED:'复核',PART_BLOCKED:'拦截',FIELD_DATA_CAPTURED:'采集',AI_INTERACTION:'问答',INCIDENT_REPORTED:'异常',COLLAB_STARTED:'协同',ANNOTATION_SYNCED:'标注',COLLAB_ARCHIVED:'归档',PROCESS_CONFIRMED:'工序',WORK_ORDER_COMPLETED:'完工'}[type] || '记录');

  let state = {
    data: null,
    role: localStorage.getItem(ROLE_KEY) || 'engineer',
    taskId: localStorage.getItem(TASK_KEY) || '',
    selectedStep: 1,
    selectedMaintenance: 0,
    selectedTwin: 0,
    selectedProcessVersion: '',
    knowledgeQuery: '',
    knowledgeCategory: '',
    knowledgeTag: '',
    selectedKnowledgeId: '',
    traceType: 'work_order',
    traceValue: '',
    traceKeyword: '',
    aiLoading: false,
    frozen: false,
    guideAction: null,
  };
  let toastTimer;

  function toast(message, kind='') {
    const el = $('toast');
    el.textContent = message;
    el.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.className = 'toast', 3200);
  }

  function save() {
    localStorage.setItem(ROLE_KEY, state.role);
    localStorage.setItem(TASK_KEY, state.taskId);
  }

  async function api(path, options={}) {
    const response = await fetch(path, {...options, headers:{'Content-Type':'application/json','X-Demo-Role':state.role,...options.headers}});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `接口错误 ${response.status}`);
    return payload;
  }

  async function refresh(showError=true) {
    try {
      const previous = state.data?.task?.current_step;
      const followCurrent = !state.data || state.selectedStep === previous;
      state.data = await api(`/api/bootstrap${state.taskId ? `?task_id=${encodeURIComponent(state.taskId)}` : ''}`);
      state.taskId = state.data.task.id;
      if (followCurrent) state.selectedStep = state.data.task.current_step;
      state.selectedStep = Math.min(state.selectedStep, state.data.task.steps.length);
      save();
      render();
      return state.data;
    } catch (error) {
      if (showError) toast(`数据加载失败：${error.message}`, 'danger');
      return null;
    }
  }

  function go(view) {
    document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === `${view}View`));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
    scrollTo({top:0, behavior:'smooth'});
  }

  async function selectTask(id) {
    if (id === state.taskId) return;
    state.taskId = id;
    state.selectedStep = 1;
    await refresh();
    toast(`当前工单已切换为 ${id}`);
  }

  function progressFor(task) {
    if (task.status === 'completed') return 100;
    return Math.round(Math.max(0, task.current_step - 1) / 8 * 100);
  }

  function nextAction(task) {
    if (task.status === 'draft') return '由管理端下发到目标AR终端';
    if (task.status === 'pending') return '现场人员扫描工单码领取';
    if (task.status === 'paused') return '处理异常或进入远程专家支持';
    if (task.status === 'completed') return '查看全过程记录和归档结果';
    return `执行主要工序 ${task.current_step}：${task.steps[task.current_step-1]?.title || ''}`;
  }

  function render() {
    if (!state.data) return;
    $('roleSelect').value = state.role;
    text('activeDevice', state.data.device.id);
    text('sidebarTask', state.data.task.id);
    text('sidebarProcess', `工艺 ${state.data.task.process_version}`);
    renderDashboard();
    renderWorkOrder();
    renderProcess();
    renderKnowledge();
    renderExpert();
    renderMaintenance();
    renderTwin();
    renderTrace();
    renderOperations();
  }

  function renderGuide() {
    const data = state.data, task = data.task;
    const stages = [
      {label:'创建工单', done:true},
      {label:'下发AR端', done:task.status !== 'draft'},
      {label:'领取与执行', done:['running','paused','completed'].includes(task.status)},
      {label:'零部件防错', done:data.partIdentifications.some(item=>item.result==='matched')},
      {label:'现场数据', done:data.observations.length > 0},
      {label:'智能问答', done:data.aiInteractions.length > 0},
      {label:'专家协同', done:data.sessions.length > 0},
      {label:'完工追溯', done:task.status === 'completed'},
    ];
    const next = stages.findIndex(item => !item.done);
    $('demoGuide').innerHTML = stages.map((item,index) => `<div class="guide-step ${item.done?'done':''} ${index===next?'current':''}"><span>${index+1}</span><strong>${item.label}</strong></div>`).join('');
    let title = '完整业务流程已完成', description = '工单、工序、现场数据、问答、专家协同和完工记录已统一归档。', actionLabel = '查看全过程追溯';
    let action = () => go('trace');
    if (next === 1) { title='下发装配工单'; description='管理端把工艺版本、八个主要工序和作业内容发送给目标AR终端。'; actionLabel='下发到AR终端'; action=dispatchTask; }
    else if (next === 2) { title='AR端领取并开始执行'; description='打开AR端模拟器，扫描工单码领取，系统将实时同步终端与人员。'; actionLabel='打开AR端联动'; action=openTerminal; }
    else if (next === 3) { title='识别零部件并进行装配防错'; description='AR端读取编码，平台按照产品型号、装配工单、当前工序和BOM逐项校验，确认正确件并拦截错误件。'; actionLabel='演示编码校验'; action=openTerminal; }
    else if (next === 4) { title='采集现场数据'; description='在AR端上传现场照片，管理端将实时收到记录并关联当前工单和工序。'; actionLabel='进入AR端采集'; action=openTerminal; }
    else if (next === 5) { title='调用受控知识问答'; description='问题自动携带产品、工艺版本和当前工序，并返回资料引用或明确拒答。'; actionLabel='进入智能知识'; action=()=>go('knowledge'); }
    else if (next === 6) { title='上报异常并开展专家协同'; description='AR端提交异常，专家端接入第一视角、冻屏标注、发送资料并归档处置意见。'; actionLabel=task.status==='paused'?'进入远程专家':'先在AR端上报异常'; action=task.status==='paused'?()=>go('expert'):openTerminal; }
    else if (next === 7) { title='完成剩余主要工序'; description='AR端逐工序确认，最后形成整架完工状态和全过程记录。'; actionLabel='继续AR端执行'; action=openTerminal; }
    text('guideTitle', title); text('guideDescription', description); text('guideAction', actionLabel); state.guideAction = action;
  }

  function renderDashboard() {
    const data = state.data, task = data.task, tasks = data.tasks;
    text('metricTaskCount', tasks.length);
    text('metricRunning', tasks.filter(item => item.status === 'running').length);
    text('metricIncident', tasks.filter(item => item.status === 'paused').length);
    text('metricIncidentText', data.incidents.some(item => item.status !== 'closed') ? '存在待处置异常' : '无未关闭异常');
    text('metricDevices', data.devices.filter(item => item.status === 'online').length);
    $('taskBoard').innerHTML = tasks.map(item => {
      const [label, cls] = statusInfo(item.status), pct = progressFor(item);
      return `<button class="task-board-row ${item.id===task.id?'selected':''}" data-task-id="${esc(item.id)}"><span><strong>${esc(item.product)}</strong><small>${esc(item.id)}</small></span><span><b>${esc(item.station)}</b><small>${esc(item.operator)} · ${esc(item.device_id)}</small></span><div><div class="mini-progress"><i style="width:${pct}%"></i></div><small>${pct}% · 工序 ${item.current_step}/8</small></div><em class="badge ${cls}">${label}</em></button>`;
    }).join('');
    document.querySelectorAll('[data-task-id]').forEach(el => el.onclick = () => selectTask(el.dataset.taskId));
    const [statusLabel, statusClass] = statusInfo(task.status), pct = progressFor(task);
    text('selectedProduct', `${task.product} · ${task.model}`); text('taskStatusBadge', statusLabel); $('taskStatusBadge').className=`badge ${statusClass}`;
    $('selectedTaskSummary').innerHTML = `<div><dt>工单编号</dt><dd>${esc(task.id)}</dd></div><div><dt>工位</dt><dd>${esc(task.station)}</dd></div><div><dt>操作员/终端</dt><dd>${esc(task.operator)} / ${esc(task.device_id)}</dd></div><div><dt>工艺快照</dt><dd>${esc(task.process_version)} · SHA-7C29</dd></div>`;
    text('dashboardPercent', `${pct}%`); $('dashboardProgress').style.width=`${pct}%`; text('dashboardNext', nextAction(task));
    $('dispatchTask').hidden = task.status !== 'draft';
    const flow = [
      ['创建工单','WORK_ORDER_CREATED'],['下发AR端','WORK_ORDER_DISPATCHED'],['现场领取','WORK_ORDER_CLAIMED'],['零部件校验','PART_IDENTIFIED'],['现场采集','FIELD_DATA_CAPTURED'],['知识问答','AI_INTERACTION'],['异常处置','INCIDENT_REPORTED'],['专家协同','COLLAB_STARTED'],['完工归档','WORK_ORDER_COMPLETED']
    ];
    const types = new Set(data.events.map(item => item.type));
    $('businessFlow').innerHTML = flow.map(([label,type]) => `<div class="flow-node ${types.has(type)?'done':''}"><span></span><strong>${label}</strong><small>${types.has(type)?'已形成记录':'待发生'}</small></div>`).join('');
    text('flowRecordCount', `${data.events.length} 条记录`);
    $('dashboardEvents').innerHTML = data.events.slice(-4).reverse().map(item => `<div class="compact-event"><time>${fmtTime(item.created_at)}</time><strong>${esc(item.title)}</strong><p>${esc(item.detail)}</p></div>`).join('') || '<div class="empty-state">等待业务事件</div>';
    renderGuide();
  }

  function renderWorkOrder() {
    const task = state.data.task, steps = task.steps, step = steps[state.selectedStep-1];
    text('taskContext', `${task.id} · ${task.product} · ${task.station}`); text('stepCounter', `主要工序 ${step.step_no} / ${steps.length}`);
    $('stepList').innerHTML = steps.map(item => `<li class="step-item ${item.status==='completed'?'done':''} ${item.step_no===task.current_step?'current':''}" data-step="${item.step_no}"><strong>${item.title}</strong><span>${item.status==='completed'?'已确认':item.step_no===task.current_step?'AR端当前工序':'待执行'}</span></li>`).join('');
    document.querySelectorAll('[data-step]').forEach(el => el.onclick = () => {state.selectedStep=Number(el.dataset.step); renderWorkOrder();});
    text('currentStepCode', `主要工序 ${String(step.step_no).padStart(2,'0')}`); text('currentStepTitle', step.title); text('currentInstruction', step.instruction); text('currentParameter', step.parameter); text('currentSafety', step.safety); text('hudStep', `${String(step.step_no).padStart(2,'0')} / ${String(steps.length).padStart(2,'0')}`); text('hudInstruction', step.instruction);
    const currentState = step.status==='completed'?'已完成':task.status==='draft'?'待管理端下发':task.status==='pending'?'待AR端领取':task.status==='paused'?'异常暂停':step.step_no===task.current_step?'现场执行中':'待执行';
    text('currentStepState', currentState); $('currentStepState').className=`badge ${step.status==='completed'?'badge-success':task.status==='paused'?'badge-danger':'badge-running'}`;
    $('monitorSummary').innerHTML = `<div><dt>架次/工单</dt><dd>${esc(task.product)}<br>${esc(task.id)}</dd></div><div><dt>现场位置</dt><dd>${esc(task.station)}</dd></div><div><dt>操作员/AR终端</dt><dd>${esc(task.operator)} / ${esc(task.device_id)}</dd></div><div><dt>工艺快照</dt><dd>${esc(task.process_version)} · SHA-7C29</dd></div>`;
    const identifications = state.data.partIdentifications || [];
    text('identificationCount', `${identifications.length} 条`);
    $('identificationList').innerHTML = identifications.length ? identifications.slice().reverse().map(item => {
      const status={matched:['匹配通过','matched'],needs_review:['人工复核','review'],blocked:['错误拦截','blocked']}[item.result]||[item.result,''];
      const checks=[['型号',item.checks.model],['工单',item.checks.work_order],['工序',item.checks.process],['BOM',item.checks.bom]];
      return `<div class="identification-row ${status[1]}"><div><strong>${esc(item.component)}</strong><span>${esc(item.raw_code)}</span></div><em>${status[0]}</em><div class="identification-checks">${checks.map(([label,pass])=>`<i class="${pass?'pass':'fail'}">${label}</i>`).join('')}</div><small>工序 ${item.step_no} · ${fmtTime(item.created_at)}<br>${esc(item.message)}</small></div>`;
    }).join('') : '<div class="empty-state">等待AR端识别零部件编码并完成型号、工单、工序和BOM校验</div>';
    const observations = state.data.observations || [];
    text('observationCount', `${observations.length} 条`);
    $('observationList').innerHTML = observations.length ? observations.slice().reverse().map(item => `<div class="observation-row"><i>${item.kind==='photo'?'照片':item.kind==='measurement'?'测量':'扫码'}</i><div><strong>${esc(item.label)}</strong><span>${esc(item.value)}</span><small>工序 ${item.step_no} · ${esc(item.device_id)} · ${fmtTime(item.created_at)}</small></div></div>`).join('') : '<div class="empty-state">等待AR端回传结构件扫码、现场照片或测量数据</div>';
    $('monitorEvents').innerHTML = state.data.events.slice(-5).reverse().map(item => `<div class="expert-event"><time>${fmtTime(item.created_at)}</time><p><strong>${esc(item.title)}</strong><br>${esc(item.detail)}</p></div>`).join('');
    dispatchEvent(new CustomEvent('ar-stage-changed',{detail:{stage:step.step_no,media:step.media}}));
  }

  function renderProcess() {
    const data = state.data;
    if(!state.selectedProcessVersion)state.selectedProcessVersion=data.processVersions.find(item=>item.status==='draft')?.version||data.task.process_version;
    text('processVersionCount',`${data.processVersions.length}个`);
    $('processVersions').innerHTML = data.processVersions.map(version => { const [label,cls]=statusInfo(version.status); const action=version.status==='draft'?`<button class="button button-secondary process-action" data-version="${version.version}" data-action="publish">发布</button>`:version.status==='archived'?`<button class="button button-secondary process-action" data-version="${version.version}" data-action="rollback">回滚</button>`:''; return `<div class="data-row ${version.version===state.selectedProcessVersion?'selected':''}" data-process-version="${esc(version.version)}"><div><strong>${esc(version.version)} · ${esc(version.checksum)}</strong><small>${esc(version.change_note)}</small></div><div class="data-actions"><span class="badge ${cls}">${label}</span>${action}</div></div>`; }).join('');
    document.querySelectorAll('[data-process-version]').forEach(el=>el.onclick=event=>{if(event.target.closest('button'))return;state.selectedProcessVersion=el.dataset.processVersion;renderProcess();});
    document.querySelectorAll('.process-action').forEach(el => el.onclick = () => processAction(el.dataset.version,el.dataset.action));
    $('processScope').innerHTML = `<div><dt>工艺名称</dt><dd>${esc(data.process.name)}</dd></div><div><dt>业务范围</dt><dd>${esc(data.process.scope)}</dd></div><div><dt>编制依据</dt><dd>${esc(data.process.standard)}</dd></div><div><dt>执行快照</dt><dd>工单创建后锁定工艺和内容版本</dd></div>`;
    const sourceSteps=(data.processSteps||[]).filter(step=>step.version===state.selectedProcessVersion).map(step=>({...step,status:data.task.steps.find(item=>item.step_no===step.step_no)?.status||'configured'}));
    $('processStepList').innerHTML='<div class="process-step-row head"><span>序号</span><span>工序名称</span><span>作业说明</span><span>工艺与质量要求</span><span>状态</span></div>'+sourceSteps.map(step=>`<button class="process-step-row ${step.step_no===state.selectedStep?'selected':''}" data-process-step="${step.step_no}"><span>${String(step.step_no).padStart(2,'0')}</span><strong>${esc(step.title)}</strong><span>${esc(step.instruction)}</span><span>${esc(step.parameter)}</span><b>${step.status==='completed'?'已执行':'已配置'}</b></button>`).join('');
    document.querySelectorAll('[data-process-step]').forEach(el=>el.onclick=()=>{state.selectedStep=Number(el.dataset.processStep);renderProcess();});
    text('contentPackageCount', `${data.contentPackages.length}个`);
    $('contentPackages').innerHTML = data.contentPackages.map(item => `<div class="content-package"><span>${String(item.process_no).padStart(2,'0')}</span><div><strong>${esc(item.title)}</strong><small>${item.formats.map(format => ({text:'文字',image:'图像',video:'动画','3d':'三维'}[format]||format)).join(' · ')}</small></div><b>${esc(item.terminal_variant)}</b></div>`).join('');
  }

  function renderKnowledge() {
    const data = state.data, service = data.integrations.knowledge, live = service.configured && service.online,docs=data.knowledgeDocuments||[];
    text('knowledgeServiceBadge', live?'在线':'本地演示'); $('knowledgeServiceBadge').className=`badge ${live?'badge-success':'badge-running'}`;
    const categories=[...new Set(docs.map(item=>item.category).filter(Boolean))],tags=[...new Set(docs.flatMap(item=>String(item.tags||'').split(',')).map(x=>x.trim()).filter(Boolean))];
    $('knowledgeCategorySummary').innerHTML=categories.map(category=>`<button data-knowledge-category="${esc(category)}"><strong>${category}</strong><span>${docs.filter(item=>item.category===category).length}份资料</span></button>`).join('');
    $('knowledgeCategoryFilter').innerHTML='<option value="">全部分类</option>'+categories.map(x=>`<option ${x===state.knowledgeCategory?'selected':''}>${esc(x)}</option>`).join('');
    $('knowledgeTagFilter').innerHTML='<option value="">全部标签</option>'+tags.map(x=>`<option ${x===state.knowledgeTag?'selected':''}>${esc(x)}</option>`).join('');
    $('knowledgeSearch').value=state.knowledgeQuery;
    document.querySelectorAll('[data-knowledge-category]').forEach(el=>el.onclick=()=>{state.knowledgeCategory=el.dataset.knowledgeCategory;renderKnowledge();});
    const q=state.knowledgeQuery.toLowerCase(),filtered=docs.filter(item=>(!q||`${item.title} ${item.tags} ${item.section}`.toLowerCase().includes(q))&&(!state.knowledgeCategory||item.category===state.knowledgeCategory)&&(!state.knowledgeTag||String(item.tags).split(',').map(x=>x.trim()).includes(state.knowledgeTag)));
    text('knowledgeCount', `${filtered.length} / ${docs.length} 条`);
    $('knowledgeList').innerHTML = '<div class="knowledge-row knowledge-doc-row head"><span>资料名称</span><span>分类与标签</span><span>版本/适用范围</span><span>索引状态</span><span>操作</span></div>' + filtered.map(item => `<div class="knowledge-row knowledge-doc-row"><div><strong>${esc(item.title)}</strong><small>${esc(item.file_name)} · ${esc(item.file_size)}</small></div><div><span>${esc(item.category)}</span><small>${esc(item.tags)}</small></div><div><span>${esc(item.version)}</span><small>${esc(item.section)}</small></div><b>${esc(item.index_status||'待索引')}</b><div class="knowledge-actions"><button data-knowledge-view="${item.id}">查看</button><a href="/api/knowledge/documents/${item.id}/download">下载</a></div></div>`).join('') || '<div class="empty-state">未找到符合条件的知识资料</div>';
    document.querySelectorAll('[data-knowledge-view]').forEach(el=>el.onclick=()=>openKnowledgeDetail(el.dataset.knowledgeView));
  }

  function renderExpert() {
    const task=state.data.task, session=state.data.sessions.find(item=>item.status==='active') || state.data.sessions[0], active=session?.status==='active', incident=state.data.incidents.find(item=>item.status!=='closed');
    text('expertTaskId', task.id); text('expertCurrentStep', task.steps[task.current_step-1]?.title || '已完成'); text('expertIncidentState', incident?`${incident.type} · 待处置`:'无未关闭异常'); text('expertProvider', session?.provider || '自建远程专家服务');
    text('expertStatusBadge', active?'协同中':session?'已归档':'未建立会话'); $('expertStatusBadge').className=`badge ${active?'badge-success':'badge-neutral'}`; $('startExpert').disabled=active;
    ['freezeFrame','drawAnnotation','sendDocument','sendAdvice','endExpert'].forEach(id => $(id).disabled=!active);
    text('videoStatusText', active?`会话 ${session.id} · 双向音视频已连接`:session?`会话已归档 · ${session.recording_index}`:'第一视角预览 · 等待现场呼叫');
    const annotations=session?.annotations || [], latest=annotations.at(-1), frozen=state.frozen || annotations.some(item=>item.kind==='freeze');
    $('frozenBadge').hidden=!frozen; $('expertVideoFrame').classList.toggle('frozen',frozen);
    $('annotationLayer').innerHTML = annotations.some(item=>item.kind==='annotation') ? '<div class="annotation-box"><span>核对接口与标识</span></div>' : '';
    const items=[]; if(session) items.push({at:session.started_at,text:`连接${session.expert}，已自动携带工单、工序和异常上下文。`}); annotations.forEach(item=>items.push({at:item.created_at,text:item.payload.text})); if(session?.ended_at) items.push({at:session.ended_at,text:`会话已结束，录像索引 ${session.recording_index} 已归档。`});
    $('expertTimeline').innerHTML=items.length?items.map(item=>`<div class="expert-event"><time>${fmtTime(item.at)}</time><p>${esc(item.text)}</p></div>`).join(''):'<div class="empty-state">AR端发起请求后，专家可接入、冻屏标注、发送资料并填写处置意见</div>';
  }

  function renderMaintenance() {
    const cases=state.data.maintenanceCases || [], integration=state.data.maintenanceIntegration;
    text('maintenanceSourceSystem',integration.source_system);text('maintenanceSyncStatus',integration.interface_status==='online'?'接口正常':'接口异常');
    $('maintenanceSyncStatus').className=`badge ${integration.interface_status==='online'?'badge-success':'badge-danger'}`;
    $('maintenanceWorkOrder').innerHTML=`<div><dt>维修工单</dt><dd>${esc(integration.work_order.id)} · ${esc(integration.work_order.status)}</dd></div><div><dt>设备档案</dt><dd>${esc(integration.equipment.id)} · ${esc(integration.equipment.model)}</dd></div><div><dt>故障现象</dt><dd>${esc(integration.work_order.symptom)}</dd></div>`;
    state.selectedMaintenance=Math.min(state.selectedMaintenance,Math.max(cases.length-1,0));
    $('maintenanceCaseList').innerHTML=cases.map((item,index)=>`<button class="maintenance-case ${index===state.selectedMaintenance?'selected':''}" data-case-index="${index}"><span>${esc(item.component)}</span><strong>${esc(item.symptom)}</strong><small>${esc(item.id)} · ${esc(item.status)}</small></button>`).join('') || '<div class="empty-state">未找到匹配案例</div>';
    document.querySelectorAll('[data-case-index]').forEach(el=>el.onclick=()=>{state.selectedMaintenance=Number(el.dataset.caseIndex);renderMaintenance();});
    const selected=cases[state.selectedMaintenance];
    if(!selected){text('maintenanceCaseTitle','无匹配案例');text('maintenanceCaseStatus','--');$('maintenanceCaseDetail').innerHTML='';return;}
    text('maintenanceCaseTitle',`${selected.component} · ${selected.symptom}`);text('maintenanceCaseStatus',selected.status);$('maintenanceCaseStatus').className=`badge ${selected.status==='已审核'?'badge-success':'badge-running'}`;
    $('maintenanceCaseDetail').innerHTML=`<div><dt>设备</dt><dd>${esc(selected.equipment)}</dd></div><div><dt>可能原因</dt><dd>${esc(selected.cause)}</dd></div><div><dt>处置方案</dt><dd>${esc(selected.resolution)}</dd></div><div><dt>备件参考</dt><dd>${esc(selected.parts)}</dd></div><div><dt>案例来源</dt><dd>${selected.source_task==='历史案例'?'维修管理系统历史工单':esc(selected.source_task)}</dd></div><div><dt>回写目标</dt><dd>${esc(integration.work_order.id)} · ${esc(integration.writeback)}</dd></div>`;
  }

  function renderTwin() {
    const twin=state.data.digitalTwin, metrics=twin.metrics, objects=twin.objects;
    text('twinLineName',twin.line);text('twinSourceSystem',twin.source_system);text('twinWorkOrders',objects.length+2);text('twinRunning',metrics.running);text('twinAlerts',metrics.alerts);text('twinDevices',metrics.online_terminals);text('twinUpdated',`同步 ${fmtTime(twin.updated_at)}`);
    $('twinModelInfo').innerHTML=`<div><dt>产线模型</dt><dd>${esc(twin.model_id)} · ${esc(twin.model_version)}</dd></div><div><dt>模型格式</dt><dd>${esc(twin.model_format)}</dd></div><div><dt>实时数据</dt><dd>${esc(twin.data_interface)}</dd></div>`;
    state.selectedTwin=Math.min(state.selectedTwin,Math.max(objects.length-1,0));
    $('twinLine').innerHTML=objects.map((item,index)=>`<button class="twin-station ${item.alert?'alert':''} ${index===state.selectedTwin?'selected':''}" data-twin-index="${index}"><span>${esc(item.station.split('·').pop().trim())}</span><strong>${esc(item.product)}</strong><i><b style="width:${item.progress}%"></b></i><small>${item.alert?'异常暂停':`${item.progress}% · ${statusInfo(item.status)[0]}`}</small></button>`).join('');
    document.querySelectorAll('[data-twin-index]').forEach(el=>el.onclick=()=>{state.selectedTwin=Number(el.dataset.twinIndex);renderTwin();});
    const selected=objects[state.selectedTwin]; if(!selected)return;
    text('twinObjectTitle',`${selected.station} · ${selected.product}`); $('twinObjectDetail').innerHTML=`<div><dt>孪生对象编码</dt><dd>${esc(selected.twin_object_id)}</dd></div><div><dt>装配状态</dt><dd>${statusInfo(selected.status)[0]}</dd></div><div><dt>装配进度</dt><dd>${selected.progress}%</dd></div><div><dt>运行参数</dt><dd>液压 ${selected.hydraulic_pressure} MPa · 温度 ${selected.temperature} ℃</dd></div><div><dt>现场告警</dt><dd>${selected.alert?'存在待处置异常':'无活动告警'}</dd></div><div><dt>数据来源</dt><dd>${esc(twin.source_system)} · ${fmtTime(twin.updated_at)}</dd></div>`;
  }

  function renderTrace() {
    const data=state.data, task=data.task, [label,cls]=statusInfo(task.status);
    const options={work_order:[{value:task.id,label:`${task.id} · ${task.product}`}],process:task.steps.map(step=>({value:String(step.step_no),label:`工序${step.step_no} · ${step.title}`})),device:data.devices.map(device=>({value:device.id,label:`${device.id} · ${device.name}`})),person:[...new Set([task.operator,...data.events.map(item=>item.actor.split('/')[0])].filter(Boolean))].map(name=>({value:name,label:name}))};
    if(!state.traceValue||!options[state.traceType].some(item=>item.value===state.traceValue))state.traceValue=options[state.traceType][0]?.value||'';
    $('traceSubjectType').value=state.traceType;$('traceSubjectValue').innerHTML=options[state.traceType].map(item=>`<option value="${esc(item.value)}" ${item.value===state.traceValue?'selected':''}>${esc(item.label)}</option>`).join('');$('traceKeyword').value=state.traceKeyword;
    const keyword=state.traceKeyword.toLowerCase();const events=data.events.filter(item=>{const content=`${item.title} ${item.detail} ${item.actor} ${item.source}`.toLowerCase();const subject=state.traceType==='work_order'||(state.traceType==='process'&&(content.includes(`工序${state.traceValue}`)||content.includes(`工序 ${state.traceValue}`)))||(state.traceType==='device'&&content.includes(state.traceValue.toLowerCase()))||(state.traceType==='person'&&content.includes(state.traceValue.toLowerCase()));return subject&&(!keyword||content.includes(keyword));});
    text('traceTaskId',task.id);text('traceStatusBadge',label);$('traceStatusBadge').className=`badge ${cls}`;
    $('traceSummary').innerHTML=`<div><dt>当前追溯主体</dt><dd>${esc($('traceSubjectValue').selectedOptions[0]?.textContent||'--')}</dd></div><div><dt>工艺快照</dt><dd>${esc(task.process_version)} / SHA-7C29</dd></div><div><dt>AR终端/人员</dt><dd>${esc(task.device_id)} / ${esc(task.operator)}</dd></div><div><dt>匹配事件</dt><dd>${events.length} 条</dd></div>`;
    $('traceEvents').innerHTML=events.slice().reverse().map(item=>`<div class="trace-event"><div class="trace-time">${fmtTime(item.created_at)}</div><div class="trace-line"></div><div class="trace-body"><strong>${esc(item.title)}</strong><p>${esc(item.detail)}</p></div><div class="trace-meta">${eventLabel(item.type)}<br>${esc(item.actor)}</div></div>`).join('');
    const recordings=(data.recordings||[]).filter(item=>state.traceType==='work_order'||(state.traceType==='process'&&String(item.step_no)===state.traceValue)||(state.traceType==='device'&&item.device_id===state.traceValue)||(state.traceType==='person'&&item.operator.includes(state.traceValue)));
    text('recordingCount',`${recordings.length}段`);$('recordingList').innerHTML=recordings.map((item,index)=>`<article class="recording-item"><video controls preload="metadata" poster="${item.poster}"><source src="${item.path}" type="video/mp4"></video><div><strong>${esc(item.title)}</strong><span>${esc(item.id)} · 工序${item.step_no}</span><small>${esc(item.device_id)} · ${esc(item.operator)} · ${esc(item.duration)}</small></div></article>`).join('')||'<div class="empty-state">当前主体没有关联的AR录像</div>';
  }

  function renderOperations() {
    const data=state.data, device=data.device, integrations=data.integrations;
    text('healthStatus','正常');text('auditCount',data.auditCount);text('opsKnowledgeStatus',integrations.knowledge.online?'在线':'本地演示');text('opsCollabStatus',integrations.collaboration.online?'正常':'异常');
    text('deviceTitleName',`${device.id} · ${device.name}`);text('deviceStatusBadge',device.status==='online'?'在线':'离线');$('deviceStatusBadge').className=`badge ${device.status==='online'?'badge-success':'badge-neutral'}`;
    $('deviceDetails').innerHTML=`<div><dt>当前电量</dt><dd>${device.battery}%</dd></div><div><dt>终端适配</dt><dd>${esc(device.adapter)}</dd></div><div><dt>工单识别</dt><dd>${esc(device.config.identification||'工单二维码')}</dd></div><div><dt>最后心跳</dt><dd>${fmtTime(device.last_seen)}</dd></div>`;
    $('deviceCapabilities').innerHTML=device.capabilities.map(item=>`<span>${esc(capabilityNames[item]||item)}</span>`).join('');
    $('serviceList').innerHTML=`<li><span><i class="service-indicator good"></i>工单与工艺服务</span><strong>正常</strong></li><li><span><i class="service-indicator ${integrations.knowledge.online?'good':''}"></i>企业知识服务</span><strong>${integrations.knowledge.online?'在线':'本地演示'}</strong></li><li><span><i class="service-indicator good"></i>AR终端适配服务</span><strong>正常</strong></li><li><span><i class="service-indicator good"></i>远程专家服务</span><strong>正常</strong></li><li><span><i class="service-indicator good"></i>数字孪生接口</span><strong>样例数据</strong></li><li><span><i class="service-indicator good"></i>事件与审计存储</span><strong>正常</strong></li>`;
  }

  async function askKnowledge(question) {
    question=question.trim(); if(!question||state.aiLoading)return; state.aiLoading=true; text('knowledgeTestResult','正在检索企业知识库...');
    try { const result=await api('/api/ai/ask',{method:'POST',body:JSON.stringify({task_id:state.taskId,question,step_no:state.data.task.current_step})}); $('knowledgeTestResult').innerHTML=`<strong>${esc(result.answer)}</strong><br><small>${result.citations?.length?'引用：'+result.citations.map(item=>`${esc(item.title)} ${esc(item.section)}`).join('；'):'资料不足，未返回引用'} · ${result.degraded?'本地演示':'企业知识服务'} · ${result.latency_ms}ms</small>`; await refresh(false); } catch(error){text('knowledgeTestResult',error.message);} finally{state.aiLoading=false;}
  }

  async function dispatchTask() { try { await api(`/api/tasks/${state.taskId}/dispatch`,{method:'POST',body:JSON.stringify({device_id:state.data.task.device_id})}); await refresh(false); toast('装配工单已下发，AR端可扫码领取。'); } catch(error){toast(error.message,'danger');} }
  function openTerminal(target='terminal.html') {
    if(typeof target!=='string')target='terminal.html';
    const panel=$('terminalFloat'),frame=$('terminalPreviewFrame'),src=target==='twin-terminal.html'?`${target}?task_id=${encodeURIComponent(state.taskId)}&embed=1`:`terminal.html?task_id=${encodeURIComponent(state.taskId)}&embed=1`;
    if(!frame.src.includes(`task_id=${encodeURIComponent(state.taskId)}`))frame.src=src;
    else if(!frame.src.includes(target))frame.src=src;
    text('terminalFloatTitle',target==='twin-terminal.html'?'AR产线透视':'AR眼镜端模拟器');
    text('terminalFloatKicker',target==='twin-terminal.html'?'数字孪生模型与实时数据 · 拖动此处移动':'双端实时联动 · 拖动此处移动');
    panel.hidden=false;panel.classList.remove('minimized');$('terminalFloatMinimize').textContent='_';
    panel.style.zIndex=String(Date.now()%100000+100);
  }
  async function startExpert() { const incident=state.data.incidents.find(item=>item.status!=='closed'); try { await api('/api/collaboration',{method:'POST',body:JSON.stringify({task_id:state.taskId,incident_id:incident?.id})}); await refresh(false); toast('专家已接入当前工单。'); } catch(error){toast(error.message,'danger');} }
  async function annotation(kind,message) { const session=state.data.sessions.find(item=>item.status==='active'); if(!session)return; try { await api(`/api/collaboration/${session.id}/annotations`,{method:'POST',body:JSON.stringify({kind,payload:{x:.47,y:.58,text:message}})}); if(kind==='freeze')state.frozen=true; await refresh(false); toast('专家内容已同步到AR端并归档。'); } catch(error){toast(error.message,'danger');} }
  async function endExpert() { const session=state.data.sessions.find(item=>item.status==='active'); if(!session)return; try { await api(`/api/collaboration/${session.id}/end`,{method:'POST',body:JSON.stringify({resolution:$('adviceInput').value})}); state.frozen=false; await refresh(false); toast('协同会话、录像索引和处置意见已归档。'); } catch(error){toast(error.message,'danger');} }
  async function processAction(version,action) { if(!['engineer','admin'].includes(state.role))return toast('请切换为工艺工程师或系统管理员。','warning'); try { await api(`/api/processes/${encodeURIComponent(version)}/${action}`,{method:'POST',body:'{}'}); await refresh(false); toast('工艺版本已更新，执行中工单保留原快照。'); } catch(error){toast(error.message,'danger');} }

  function openProcessVersionDialog(){if(!['engineer','admin'].includes(state.role))return toast('请切换为工艺工程师或系统管理员。','warning');$('baseProcessVersion').innerHTML=state.data.processVersions.map(item=>`<option value="${esc(item.version)}">${esc(item.version)} · ${statusInfo(item.status)[0]}</option>`).join('');$('processVersionDialog').showModal();}
  async function createProcessVersion(event){event.preventDefault();try{const result=await api('/api/processes',{method:'POST',body:JSON.stringify({version:$('newProcessVersion').value.trim(),base_version:$('baseProcessVersion').value,change_note:$('newProcessNote').value.trim()})});state.selectedProcessVersion=result.version;$('processVersionDialog').close();await refresh(false);go('process');toast(`工艺草稿 ${result.version} 已创建。`);}catch(error){toast(error.message,'danger');}}
  function openProcessStepDialog(){const version=state.data.processVersions.find(item=>item.version===state.selectedProcessVersion);if(!version||version.status!=='draft')return toast('请选择草稿工艺版本后编辑。','warning');const step=state.data.processSteps.find(item=>item.version===state.selectedProcessVersion&&item.step_no===state.selectedStep);if(!step)return toast('请选择需要编辑的工序。','warning');text('processStepDialogTitle',`编辑 ${state.selectedProcessVersion} · 工序${step.step_no}`);$('editProcessStepNo').value=step.step_no;$('editProcessStepTitle').value=step.title;$('editProcessInstruction').value=step.instruction;$('editProcessParameter').value=step.parameter;$('editProcessSafety').value=step.safety;$('processStepDialog').showModal();}
  async function saveProcessStep(event){event.preventDefault();try{await api(`/api/processes/${encodeURIComponent(state.selectedProcessVersion)}/steps`,{method:'POST',body:JSON.stringify({step_no:Number($('editProcessStepNo').value),title:$('editProcessStepTitle').value,instruction:$('editProcessInstruction').value,parameter:$('editProcessParameter').value,safety:$('editProcessSafety').value,media:'text,image,3d'})});$('processStepDialog').close();await refresh(false);go('process');toast('工序内容已保存到草稿版本。');}catch(error){toast(error.message,'danger');}}

  function openKnowledgeUpload(){if(!['engineer','admin'].includes(state.role))return toast('请切换为工艺工程师或系统管理员。','warning');$('knowledgeUploadDialog').showModal();}
  async function uploadKnowledge(event){event.preventDefault();const file=$('knowledgeUploadFile').files[0];let content=$('knowledgeUploadContent').value.trim();if(file&&/\.(txt|md)$/i.test(file.name))content=await file.text();try{await api('/api/knowledge/documents',{method:'POST',body:JSON.stringify({title:$('knowledgeUploadTitle').value.trim(),category:$('knowledgeUploadCategory').value,version:$('knowledgeUploadVersion').value.trim(),tags:$('knowledgeUploadTags').value.trim(),section:$('knowledgeUploadSection').value.trim(),file_name:file?.name||`${$('knowledgeUploadTitle').value.trim()}.txt`,file_type:file?.name.split('.').pop()?.toUpperCase()||'TXT',file_size:file?`${Math.max(1,Math.round(file.size/1024))} KB`:undefined,content})});$('knowledgeUploadDialog').close();await refresh(false);go('knowledge');toast('资料已上传并登记，当前状态为待索引。');}catch(error){toast(error.message,'danger');}}
  async function openKnowledgeDetail(id){try{const item=await api(`/api/knowledge/documents/${encodeURIComponent(id)}`);state.selectedKnowledgeId=id;text('knowledgeDetailTitle',item.title);$('knowledgeDetailMeta').innerHTML=`<div><dt>分类</dt><dd>${esc(item.category)}</dd></div><div><dt>标签</dt><dd>${esc(item.tags)}</dd></div><div><dt>版本</dt><dd>${esc(item.version)}</dd></div><div><dt>适用范围</dt><dd>${esc(item.section)}</dd></div><div><dt>文件</dt><dd>${esc(item.file_name)} · ${esc(item.file_size)}</dd></div><div><dt>索引状态</dt><dd>${esc(item.index_status)}</dd></div>`;$('knowledgeDetailContent').textContent=item.content;$('knowledgeDetailDialog').showModal();}catch(error){toast(error.message,'danger');}}
  function downloadKnowledge(){if(!state.selectedKnowledgeId)return;const link=document.createElement('a');link.href=`/api/knowledge/documents/${encodeURIComponent(state.selectedKnowledgeId)}/download`;link.download='knowledge-document.txt';link.click();}

  function openCreateTask() { if(!['engineer','admin'].includes(state.role))return toast('请切换为工艺工程师或系统管理员。','warning'); const published=state.data.processVersions.filter(item=>item.status==='published'); $('newTaskProcess').innerHTML=published.map(item=>`<option value="${esc(item.version)}">${esc(item.version)} · ${esc(item.change_note)}</option>`).join(''); $('newTaskDevice').innerHTML=state.data.devices.map(item=>`<option value="${esc(item.id)}">${esc(item.id)} · ${esc(item.name)} · ${item.status==='online'?'在线':'离线'}</option>`).join(''); $('createTaskDialog').showModal(); }
  async function createTask(event) { event.preventDefault(); const submit=event.submitter; submit.disabled=true; try { const payload={product:$('newTaskProduct').value.trim(),model:$('newTaskModel').value.trim(),station:$('newTaskStation').value.trim(),process_version:$('newTaskProcess').value,device_id:$('newTaskDevice').value}; const created=await api('/api/tasks',{method:'POST',body:JSON.stringify(payload)}); state.taskId=created.task.id; save(); if($('newTaskDispatch').checked)await api(`/api/tasks/${state.taskId}/dispatch`,{method:'POST',body:JSON.stringify({device_id:payload.device_id})}); $('createTaskDialog').close(); state.selectedStep=1; await refresh(false); toast(`装配工单 ${state.taskId} 已创建${$('newTaskDispatch').checked?'并下发':''}。`); } catch(error){toast(error.message,'danger');} finally{submit.disabled=false;} }

  async function searchMaintenance(query) { try { const result=await api(`/api/maintenance/cases?q=${encodeURIComponent(query.trim())}`); state.data.maintenanceCases=result.items; state.selectedMaintenance=0; renderMaintenance(); } catch(error){toast(error.message,'danger');} }
  async function archiveMaintenanceCase() { try { await api('/api/maintenance/cases',{method:'POST',body:JSON.stringify({task_id:state.taskId})}); await refresh(false); toast('处置记录已形成待审核案例，并准备回写维修管理系统。'); go('maintenance'); } catch(error){toast(error.message,'danger');} }
  async function adminAction(action) { if(state.role!=='admin')return toast('请切换为系统管理员。','warning'); if((action==='restore'||action==='reset')&&!confirm('确认执行该操作？'))return; try { const result=await api(`/api/admin/${action}`,{method:'POST',body:'{}'}); text('backupResult',`操作成功${result.file?'：'+result.file:''}`); state.taskId='';state.selectedStep=1;await refresh(false);toast('系统管理操作完成。'); } catch(error){toast(error.message,'danger');} }
  async function resetDemo() { try { const result=await api('/api/demo/reset',{method:'POST',body:'{}'}); state.taskId=result.task_id; state.selectedStep=1; state.frozen=false; save(); go('dashboard'); await refresh(false); toast('演示数据已重置，请从“下发装配工单”开始。'); } catch(error){toast(error.message,'danger');} }

  document.querySelectorAll('.nav-item').forEach(el=>el.onclick=()=>go(el.dataset.view));
  document.querySelectorAll('[data-jump]').forEach(el=>el.onclick=()=>go(el.dataset.jump));
  document.querySelectorAll('.open-terminal').forEach(el=>el.onclick=openTerminal);
  $('openTwinTerminal').onclick=()=>openTerminal('twin-terminal.html');
  $('roleSelect').onchange=event=>{state.role=event.target.value;save();render();toast(`已切换为${roleNames[state.role]}`);};
  $('guideAction').onclick=()=>state.guideAction?.(); $('dispatchTask').onclick=dispatchTask; $('openCreateTask').onclick=openCreateTask;
  $('createTaskForm').onsubmit=createTask; $('closeCreateTask').onclick=()=>$('createTaskDialog').close(); $('cancelCreateTask').onclick=()=>$('createTaskDialog').close();
  $('createProcessVersion').onclick=openProcessVersionDialog;$('processVersionForm').onsubmit=createProcessVersion;$('closeProcessVersion').onclick=()=>$('processVersionDialog').close();$('cancelProcessVersion').onclick=()=>$('processVersionDialog').close();$('editCurrentProcessStep').onclick=openProcessStepDialog;$('processStepForm').onsubmit=saveProcessStep;$('closeProcessStep').onclick=()=>$('processStepDialog').close();$('cancelProcessStep').onclick=()=>$('processStepDialog').close();
  $('uploadKnowledge').onclick=openKnowledgeUpload;$('knowledgeUploadForm').onsubmit=uploadKnowledge;$('closeKnowledgeUpload').onclick=()=>$('knowledgeUploadDialog').close();$('cancelKnowledgeUpload').onclick=()=>$('knowledgeUploadDialog').close();$('closeKnowledgeDetail').onclick=()=>$('knowledgeDetailDialog').close();$('downloadKnowledgeDetail').onclick=downloadKnowledge;
  $('closeTerminalPreview').onclick=()=>{$('terminalFloat').hidden=true;$('terminalPreviewFrame').src='about:blank';};
  $('terminalFloatMinimize').onclick=()=>{const panel=$('terminalFloat'),minimized=panel.classList.toggle('minimized');$('terminalFloatMinimize').textContent=minimized?'□':'_';};
  function resizeTerminal(delta){const panel=$('terminalFloat'),rect=panel.getBoundingClientRect();panel.style.width=`${Math.max(480,Math.min(innerWidth-24,rect.width+delta))}px`;panel.classList.remove('minimized');}
  $('terminalFloatSmaller').onclick=()=>resizeTerminal(-140);$('terminalFloatLarger').onclick=()=>resizeTerminal(140);
  (()=>{const panel=$('terminalFloat'),bar=$('terminalFloatBar');let drag=null;bar.addEventListener('pointerdown',event=>{if(event.target.closest('button'))return;const rect=panel.getBoundingClientRect();drag={x:event.clientX-rect.left,y:event.clientY-rect.top};panel.style.left=`${rect.left}px`;panel.style.top=`${rect.top}px`;panel.style.right='auto';bar.setPointerCapture(event.pointerId);});bar.addEventListener('pointermove',event=>{if(!drag)return;const maxX=Math.max(0,innerWidth-panel.offsetWidth),maxY=Math.max(0,innerHeight-48);panel.style.left=`${Math.max(0,Math.min(maxX,event.clientX-drag.x))}px`;panel.style.top=`${Math.max(0,Math.min(maxY,event.clientY-drag.y))}px`;});bar.addEventListener('pointerup',()=>{drag=null});})();
  $('knowledgeTestForm').onsubmit=event=>{event.preventDefault();askKnowledge($('knowledgeTestInput').value);};
  $('knowledgeFilterForm').onsubmit=event=>{event.preventDefault();state.knowledgeQuery=$('knowledgeSearch').value.trim();state.knowledgeCategory=$('knowledgeCategoryFilter').value;state.knowledgeTag=$('knowledgeTagFilter').value;renderKnowledge();};
  $('startExpert').onclick=startExpert; $('freezeFrame').onclick=()=>annotation('freeze','现场画面已冻结，可进行二维标注'); $('drawAnnotation').onclick=()=>annotation('annotation','请核对该胶管标识与阀组接口对应关系'); $('sendDocument').onclick=()=>annotation('document','已发送当前受控液压原理图和接口清单PDF'); $('adviceForm').onsubmit=event=>{event.preventDefault();annotation('text',$('adviceInput').value);}; $('endExpert').onclick=endExpert;
  $('maintenanceSearchForm').onsubmit=event=>{event.preventDefault();searchMaintenance($('maintenanceSearchInput').value);}; $('archiveMaintenanceCase').onclick=archiveMaintenanceCase;
  $('traceSubjectType').onchange=event=>{state.traceType=event.target.value;state.traceValue='';renderTrace();};$('traceSubjectValue').onchange=event=>{state.traceValue=event.target.value;renderTrace();};$('traceSubjectForm').onsubmit=event=>{event.preventDefault();state.traceType=$('traceSubjectType').value;state.traceValue=$('traceSubjectValue').value;state.traceKeyword=$('traceKeyword').value.trim();renderTrace();};
  $('exportTrace').onclick=()=>{const link=document.createElement('a');link.href=`/api/trace/export?format=json&task_id=${encodeURIComponent(state.taskId)}`;link.download=`${state.taskId}-trace.json`;link.click();};
  $('sendHeartbeat').onclick=async()=>{try{await api(`/api/devices/${state.data.device.id}/heartbeat`,{method:'POST',body:JSON.stringify({battery:state.data.device.battery})});await refresh(false);toast('AR终端心跳已更新。');}catch(error){toast(error.message,'danger');}};
  $('createBackup').onclick=()=>adminAction('backup'); $('restoreBackup').onclick=()=>adminAction('restore'); $('resetSystem').onclick=()=>adminAction('reset'); $('resetDemo').onclick=resetDemo;

  refresh();
  setInterval(()=>refresh(false),5000);
})();
