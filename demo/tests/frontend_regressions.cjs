// Run the production scripts with controlled response order, without a network or browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div', text = '') {
    this.tagName = tag.toUpperCase(); this.children = []; this.textContent = text;
    this.dataset = {}; this.value = ''; this.disabled = false; this.hidden = false;
    this.open = false; this.listeners = {};
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return []; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  click() { return this.onclick?.(); }
  focus() {}
}

function environment(api = () => new Promise(() => {})) {
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const el = (tag, text, cls) => { const node = new Element(tag, text); node.className = cls || ''; return node; };
  const document = {getElementById: $, createElement: el, createDocumentFragment: () => new Element('fragment'),
    createTextNode: text => new Element('text', text), querySelectorAll: () => [], addEventListener() {}};
  const context = vm.createContext({$, el, document, api, window: {addEventListener() {}, dispatchEvent() {}},
    console, setTimeout, clearTimeout, URLSearchParams, Event, fetch: () => new Promise(() => {}),
    busy: false, current: {}, modelConfigured: true, hasGenerationBasis: () => true,
    setBusy(value) { context.busy = value; }, error() {}, clearResult() {},
    readTeachingOptions: () => ({}), refreshRuns: async () => {}, showFailedTrace() {}});
  context.window = context;
  context.addEventListener = () => {};
  context.dispatchEvent = () => {};
  function load(file) { vm.runInContext(fs.readFileSync(path.join(__dirname, '../static', file), 'utf8'), context); }
  return {$, context, load};
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const flush = () => new Promise(resolve => setImmediate(resolve));

async function markdownOrder() {
  const e = environment(); e.load('app.js');
  const tree = vm.runInContext('renderMarkdown("- 前文\\n```\\nx = 1\\n```\\n- 后文")', e.context);
  assert.deepEqual(Array.from(tree.children, n => n.tagName), ['UL', 'PRE', 'UL'], 'formula must stay between the two lists');
  assert.equal(tree.children[0].children.length, 1);
}

async function markdownFenceContent() {
  const e = environment(); e.load('app.js');
  const tree = vm.runInContext('renderMarkdown("```\\nx = 1\\n~~~\\ny = 2\\n```")', e.context);
  assert.equal(tree.children[0].children.map(n => n.textContent).join(''), 'x = 1\n~~~\ny = 2\n');
}

async function automaticLengthRemainsOptional() {
  const e = environment(); e.load('teaching.js');
  vm.runInContext('restoreTeachingOptions({duration:5, target_characters:0})', e.context);
  assert.equal(e.$('lesson-target').value, '', 'automatic length must remain blank to satisfy the optional min=400 input');
  assert.equal(vm.runInContext('readTeachingOptions().target_characters', e.context), 0);
  vm.runInContext('restoreTeachingOptions({target_characters:1200})', e.context);
  assert.equal(Number(e.$('lesson-target').value), 1200, 'explicit length must survive restoration');
  vm.runInContext('restoreTeachingOptions(undefined)', e.context);
  assert.equal(e.$('lesson-target').value, '', 'legacy lectures must not inherit an explicit length');
}

async function imagePreviewOrder() {
  const first = deferred(), second = deferred();
  const e = environment(url => url === '/api/vision/analyses' ? Promise.resolve({analyses: []}) : url.includes('/first/') ? first.promise : second.promise);
  e.load('vision.js');
  const a = vm.runInContext('showUploadImages("first")', e.context);
  const b = vm.runInContext('showUploadImages("second")', e.context);
  second.resolve({images: [{id: 'second-image', label: '第二份资料'}]}); await b;
  first.resolve({images: [{id: 'first-image', label: '第一份资料'}]}); await a;
  assert.deepEqual(Array.from(e.$('upload-preview-images').children, card => card.children[0].textContent), ['第二份资料'], 'slow old request must not replace the current document images');
}

async function uploadPreviewOrder() {
  const first = deferred(), second = deferred();
  const uploads = ['first', 'second'].map(id => ({id, title: id, filename: id+'.txt', characters: 3, created_at: new Date().toISOString()}));
  const e = environment(url => url === '/api/uploads' ? Promise.resolve({uploads}) : url.endsWith('/first') ? first.promise : second.promise);
  e.context.showUploadImages = async () => {};
  e.load('uploads.js'); await flush();
  const rows = e.$('upload-list').children;
  const a = rows[0].children[1].children[0].onclick();
  const b = rows[1].children[1].children[0].onclick();
  second.resolve({upload: {title: '第二份资料', content: 'second'}}); await b;
  first.resolve({upload: {title: '第一份资料', content: 'first'}}); await a;
  assert.equal(e.$('upload-preview-title').textContent, '第二份资料');
  assert.equal(e.$('upload-preview-content').textContent, 'second');
}

async function refreshingImagesKeepsLiveSelection() {
  const responses = [deferred(), deferred(), deferred()]; let calls = 0;
  const e = environment(() => responses[calls++].promise);
  const inputs = () => e.$('visual-evidence-options').children.flatMap(n => n.children).filter(n => n.tagName === 'INPUT');
  e.context.document.querySelectorAll = selector => inputs().filter(n => !selector.includes(':checked') || n.checked);
  e.load('vision.js');
  responses[0].resolve({analyses: [{id: 'image', label: '测试图', model: 'fixture'}]}); await flush();
  inputs()[0].checked = true;
  const refresh = vm.runInContext('refreshVisualEvidence()', e.context);
  inputs()[0].checked = false;
  responses[1].resolve({analyses: [{id: 'image', label: '测试图', model: 'fixture'}]}); await refresh;
  assert.equal(inputs()[0].checked, false, 'refresh must preserve changes made while the request was in flight');
  inputs()[0].checked = true;
  const failure = vm.runInContext('refreshVisualEvidence()', e.context);
  // A non-array response exercises the same error path as a rejected request.
  responses[2].resolve({}); await failure;
  assert.equal(inputs()[0]?.checked, true, 'failed refresh must not silently discard selected evidence');
}

async function acceptedJobSurvivesHistoryFailure() {
  let refreshes = 0, observed = false;
  const e = environment(async (url, options) => {
    if (url === '/api/health') return {database_ok: true, endpoint_format_ok: true, model_present: true};
    if (url === '/api/jobs' && options?.method === 'POST') return {id: 'accepted'};
    if (url === '/api/jobs') { if (++refreshes === 2) throw new Error('history temporarily unavailable'); return {jobs: []}; }
    if (url === '/api/jobs/accepted') { observed = true; return {status: 'failed', run_id: 1, created_at: new Date().toISOString()}; }
    throw new Error('unexpected request: ' + url);
  });
  e.load('jobs.js'); await flush();
  await e.$('generate-form').onsubmit({preventDefault() {}});
  assert.equal(observed, true, 'an accepted generation must still be watched when history refresh fails');
}

async function backgroundCompletionPreservesPage() {
  const completed = deferred();
  const e = environment(); e.load('app.js');
  let page = 'workbench';
  e.context.openWorkspacePage = next => { page = next; };
  e.$('download-format').selectedOptions = [{textContent: 'Markdown'}];
  vm.runInContext("topics={linear:{title:'测试课程'}}; current={materials:[{}]}; modelConfigured=true", e.context);
  const lecture = {id: 5, topic_key: 'linear', topic: '测试课程', student_id: 1, goal: '目标', content: '# 测试讲义',
    model: 'fixture', elapsed_seconds: 0, citation_check: {has_citations: false},
    context: {topic: {key: 'linear', title: '测试课程'}, student: {name: '测试学生', profile: ''}, materials: []}};
  e.context.api = async (url, options) => {
    if (url === '/api/health') return {database_ok: true, endpoint_format_ok: true, model_present: true};
    if (url === '/api/jobs' && options?.method === 'POST') return {id: 'accepted'};
    if (url === '/api/jobs') return {jobs: []};
    if (url === '/api/jobs/accepted') return completed.promise;
    if (url === '/api/lectures/5') return lecture;
    if (url === '/api/bootstrap') return {history: []};
    throw new Error('unexpected request: '+url);
  };
  e.load('jobs.js'); await flush();
  const submitted = e.$('generate-form').onsubmit({preventDefault() {}});
  await flush(); page = 'profiles';
  completed.resolve({status: 'succeeded', lecture_id: 5, run_id: 1, usage: {}, created_at: new Date().toISOString()});
  await submitted;
  assert.equal(e.$('lecture').hidden, false, 'completed lecture should still be rendered');
  assert.equal(page, 'profiles', 'background completion must not interrupt the page the teacher selected');
}

async function richMarkdown() {
  const e = environment(); e.load('app.js');
  const source = ['## 核心 $a^2$', '由 $P(A\\mid B)=\\frac{1}{2}$ 得 **结论** [M1]，花费 $5 和 $8。', '$$', 'x = \\frac{1}{2}', '$$',
    '| 量 | 值 |', '|---|---|', '| $p$ | 0.6 |', '```figure', '{"type":"bar_chart"}', '```', '- 项', '```figure', '{"ty'].join('\n');
  const tree = vm.runInContext('renderMarkdown(' + JSON.stringify(source) + ')', e.context);
  const kinds = Array.from(tree.children, n => n.tagName + (n.className ? '.' + n.className : ''));
  assert.deepEqual(kinds, ['H2', 'P', 'DIV.math-display', 'DIV.table-wrap', 'DIV.figure-block', 'UL', 'DIV.figure-block']);
  const paragraph = tree.children[1].children;
  assert.equal(paragraph.filter(n => n.className === 'math-inline').length, 1, 'money amounts must not become formulas');
  assert.equal(paragraph.find(n => n.className === 'math-inline').dataset.tex, 'P(A\\mid B)=\\frac{1}{2}');
  assert.ok(paragraph.some(n => n.className === 'citation'), 'citations still render next to formulas');
  assert.equal(tree.children[2].dataset.tex, 'x = \\frac{1}{2}');
  const table = tree.children[3].children[0];
  assert.deepEqual(table.children.map(row => row.children.map(cell => cell.tagName).join()), ['TH,TH', 'TD,TD']);
  assert.match(tree.children[6].children[0].textContent, /正在写入/, 'an unfinished figure block shows a placeholder while streaming');
}

async function submissionCarriesPipelineChoices() {
  const calls = [];
  const e = environment(async (url, options) => { calls.push([url, options]); return new Promise(() => {}); });
  e.load('jobs.js');
  e.$('generation-mode').value = 'single'; e.$('lecture-format').value = 'plain';
  e.$('generate-form').onsubmit({preventDefault() {}});
  await flush();
  const sent = JSON.parse(calls.find(([url, options]) => url === '/api/jobs' && options)[1].body);
  assert.equal(sent.generation_mode, 'single');
  assert.equal(sent.lecture_format, 'plain');
  e.$('generation-mode').value = ''; e.$('lecture-format').value = '';
  assert.deepEqual([e.context.generationPayload().generation_mode, e.context.generationPayload().lecture_format], ['sectioned-v1', 'rich-v1']);
}

async function classTargetFollowsScope() {
  const calls = [];
  const e = environment(async (url, options) => { calls.push([url, options]); return new Promise(() => {}); });
  e.context.workspaceScope = {courseId: () => 1, klass: () => ({id: 5, name: '一班', student_ids: [1, 2]}), classOf: () => 5, set() {}, ready: Promise.resolve()};
  e.load('app.js');
  vm.runInContext("topics={a:{title:'A',course_id:1,student_ids:[1,2,3]},b:{title:'B',course_id:2,student_ids:[1]}};" +
    "students=[{id:1,name:'甲',subtitle:''},{id:2,name:'乙',subtitle:''},{id:3,name:'丙',subtitle:''}]; current={materials:[{}]}; drawTopics(); filterStudents()", e.context);
  assert.deepEqual(e.$('topic').children.map(o => o.value), ['a'], 'only lessons of the current course are offered');
  assert.deepEqual(e.$('student').children.map(o => String(o.value)), ['class:5', '1', '2'], 'the whole class comes first, then only its students');
  e.$('student').value = 'class:5';
  e.context.api = async (url, options) => { calls.push([url, options]); return new Promise(() => {}); };
  e.load('jobs.js');
  e.$('generate-form').onsubmit({preventDefault() {}});
  await flush();
  const sent = JSON.parse(calls.find(([url, options]) => url === '/api/jobs' && options)[1].body);
  assert.equal(sent.class_id, 5);
  assert.equal('student_id' in sent, false, 'a class lecture has no student target');
}

async function knowledgeReviewMergesAndConfirms() {
  const posts = [];
  const draft = {draft: {status: 'ready', method: 'rules+model', notice: '', version: 'v1', items: [
      {name: '随机变量', chapter: '第二章', section: '2.1', summary: '定义', units: [1, 2]},
      {name: '离散型', chapter: '第二章', section: '2.1', summary: '分类', units: [3, 4]},
      {name: '泊松分布', chapter: '第二章', section: '2.2', summary: '', units: [5, 5]}]},
    units: [1, 2, 3, 4, 5].map(n => ({n, label: `第 ${n} 页`, preview: '页' + n})), library: ['泊松分布'], upload: {title: '第二章'}};
  const e = environment((url, options) => {
    if (url === '/api/courses/2/knowledge') return Promise.resolve({points: [], drafts: []});
    if (url === '/api/knowledge/drafts/u1') return Promise.resolve(draft);
    if (url === '/api/knowledge/drafts/u1/confirm') { posts.push(JSON.parse(options.body)); return Promise.resolve({created: 1, reused: 1}); }
    return new Promise(() => {});
  });
  e.context.location = {hash: '#materials/resources'};
  e.context.workspaceScope = {courseId: () => 2, course: () => ({id: 2}), ready: Promise.resolve()};
  e.context.sourceHref = url => url;
  e.load('knowledge.js'); await flush();
  await e.context.courseKnowledge.review('u1');
  assert.equal(e.$('knowledge-dialog').open, true);
  assert.equal(e.$('knowledge-dialog-summary').textContent, '共 3 条：新增 2，合并到已有 1');
  // Row 2 joins row 1: ranges combine and the summaries are kept together.
  e.$('knowledge-dialog-items').children[1].children[2].children[1].click();
  assert.equal(e.$('knowledge-dialog-items').children.length, 2);
  e.$('knowledge-dialog-chapter').value = '第二章 随机变量与分布';
  await e.$('knowledge-dialog-form').onsubmit({preventDefault() {}}); await flush();
  assert.deepEqual(posts, [{version: 'v1', items: [
    {name: '随机变量', section: '2.1', summary: '定义；分类', units: [1, 4], chapter: '第二章 随机变量与分布'},
    {name: '泊松分布', section: '2.2', summary: '', units: [5, 5], chapter: '第二章 随机变量与分布'}]}]);
  assert.equal(e.$('knowledge-dialog').open, false);
  assert.match(e.$('upload-status').textContent, /新增 1 个，合并到已有 1 个/);
}

async function studentEditKeepsItsBaseRevision() {
  let revision = 3; const posts = [];
  const record = () => ({student: {id: 7, name: '王一', subtitle: '七年级', profile: '计算稳', profile_revision: revision},
    classes: [], states: [], profile_history: []});
  const e = environment(async (url, options) => {
    if (options?.method === 'POST') { posts.push([url, JSON.parse(options.body)]); revision++; }
    return record();
  });
  const listeners = {};
  e.context.addEventListener = (name, callback) => { (listeners[name] ||= []).push(callback); };
  e.context.location = {hash: '#profiles/students'};
  e.context.students = [{id: 7, name: '王一', subtitle: '七年级', profile: '计算稳'}];
  e.context.workspaceScope = {courseId: () => 1, classId: () => 5, classOf: () => 5, refresh: async () => {},
    course: () => ({name: '七年级数学', classes: [{id: 5, name: '一班'}]}), klass: () => ({id: 5, name: '一班', student_ids: [7]})};
  e.load('student-roster.js');
  const edit = e.$('student-roster').children[0].children[1].children[0];
  assert.equal(edit.textContent, '编辑', 'each roster card offers editing beside leaving the class');
  edit.click();
  assert.equal(e.context.location.hash, 'profiles/7');
  e.context.location.hash = '#profiles/7';
  listeners.hashchange.forEach(callback => callback());
  await flush();
  assert.equal(e.$('edit-student-form').hidden, false, 'the card opens the record with the form open');
  assert.equal(e.$('edit-student-name').value, '王一');
  revision = 4;  // someone else saves meanwhile; re-reading must not move the form's base
  e.$('view-current-profile').click();
  await flush();
  assert.equal(e.$('edit-student-form').hidden, false, 're-reading the same student keeps the draft');
  e.$('edit-student-name').value = '王小一'; e.$('edit-student-recorder').value = '王老师'; e.$('edit-student-reason').value = '更正姓名';
  await e.$('edit-student-form').onsubmit({preventDefault() {}});
  assert.equal(posts.length, 1);
  assert.equal(posts[0][0], '/api/students/7/profile?course_id=1');
  assert.equal(posts[0][1].expected_revision, 3, 'a concurrent change is refused by the server, not overwritten');
  assert.equal(posts[0][1].name, '王小一');
  assert.equal(posts[0][1].confirmed, true);
  assert.equal(e.$('edit-student-form').hidden, true);
  assert.match(e.$('student-edit-status').textContent, /已保存/);
}

const cases = {knowledgeReviewMergesAndConfirms, studentEditKeepsItsBaseRevision, classTargetFollowsScope, richMarkdown, submissionCarriesPipelineChoices, markdownOrder, markdownFenceContent, automaticLengthRemainsOptional, imagePreviewOrder, uploadPreviewOrder, refreshingImagesKeepsLiveSelection, acceptedJobSurvivesHistoryFailure, backgroundCompletionPreservesPage};
(async () => {
  const name = process.argv[2]; assert.ok(cases[name], 'choose a regression case');
  await cases[name](); console.log(name + ': OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
