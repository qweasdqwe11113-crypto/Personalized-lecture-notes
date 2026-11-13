/* The current course and class, shared by every page. Pages read window.workspaceScope and
   listen for 'scope-changed' (detail: what caused it). The choice is kept by the server
   (/api/workspace-scope): a browser keeps its storage per port, and the desktop client moves to
   another port when its own is taken. This browser's copy is only a fallback, and carries a
   choice made before the server kept it over to the server once. */
(() => {
  const KEY = 'zhiye.scope';
  let courses = [], unassigned = 0, courseId = null, classId = null;
  const shape = v => v && typeof v === 'object' ? {course: v.course ?? null, classes: v.classes && typeof v.classes === 'object' ? {...v.classes} : {}} : null;
  let memory = (() => { try { return shape(JSON.parse(localStorage.getItem(KEY))); } catch { return null; } })() || {course: null, classes: {}};
  let loaded = false, sent = '', saving = Promise.resolve();
  function remember() {
    // Classes of courses that no longer exist are dropped, so the memory does not keep growing.
    const known = new Set(courses.map(c => String(c.id)));
    memory.classes = Object.fromEntries(Object.entries(memory.classes).filter(([id]) => known.has(id)));
    const body = JSON.stringify(memory);
    try { localStorage.setItem(KEY, body); } catch { /* private window */ }
    if (body === sent) return;
    sent = body;
    // In order, so an older choice never lands after a newer one; a failed save is tried again
    // with the next change.
    saving = saving.then(() => api('/api/workspace-scope', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body}))
      .catch(() => { if (sent === body) sent = ''; });
  }
  const course = () => courses.find(c => c.id === courseId) || null;
  const klass = () => course()?.classes.find(c => c.id === classId) || null;
  const courseLabel = c => (c.stage ? c.stage + ' · ' : '') + c.name;
  function draw() {
    const option = (value, label) => { const o = el('option', label); o.value = value; return o; };
    $('ctx-course').replaceChildren(...(courses.length ? courses.map(c => option(c.id, courseLabel(c))) : [option('', '尚无课程')]));
    $('ctx-course').value = String(courseId ?? '');
    $('ctx-course').disabled = !courses.length;
    const own = course()?.classes || [];
    $('ctx-class').replaceChildren(...(own.length ? own.map(c => option(c.id, `${c.name} · ${c.student_ids.length} 人`)) : [option('', '尚无班级')]));
    $('ctx-class').value = String(classId ?? '');
    $('ctx-class').disabled = !own.length;
  }
  function choose(nextCourse, nextClass) {
    const found = courses.find(c => c.id === nextCourse) || courses.find(c => c.id === memory.course) || courses[0] || null;
    const own = found?.classes || [];
    const wanted = [nextClass, memory.classes[found?.id]].find(id => own.some(c => c.id === id));
    return [found?.id ?? null, wanted ?? own[0]?.id ?? null];
  }
  function set(nextCourse, nextClass, source = 'switcher') {
    const [c, k] = choose(nextCourse, nextClass);
    const changed = c !== courseId || k !== classId;
    courseId = c; classId = k;
    if (c != null) { memory.course = c; memory.classes[c] = k; remember(); }
    draw();
    if (changed || source === 'refresh') window.dispatchEvent(new CustomEvent('scope-changed', {detail: source}));
    return changed;
  }
  async function refresh() {
    const [d, stored] = await Promise.all([api('/api/courses'),
      loaded ? undefined : api('/api/workspace-scope').then(r => shape(r.scope), () => undefined)]);
    if (!loaded) {
      loaded = true;
      // Nothing on the server yet (or it could not answer): keep this browser's copy.
      if (stored) { memory = stored; sent = JSON.stringify(stored); }
    }
    courses = d.courses; unassigned = d.unassigned_students;
    set(courseId ?? memory.course, classId ?? undefined, 'refresh');
    return d;
  }
  $('ctx-course').onchange = () => set(Number($('ctx-course').value), undefined);
  $('ctx-class').onchange = () => set(courseId, Number($('ctx-class').value));
  window.workspaceScope = {
    get courses() { return courses; },
    get unassigned() { return unassigned; },
    course, klass, courseLabel,
    courseId: () => courseId,
    classId: () => classId,
    classStudents: () => klass()?.student_ids || [],
    courseLessonKeys: () => course()?.lesson_keys || [],
    classOf(studentId, inCourse = courseId) {
      const c = courses.find(x => x.id === inCourse);
      return c?.classes.find(k => k.student_ids.includes(studentId))?.id ?? null;
    },
    set, refresh,
    ready: null,
  };
  window.workspaceScope.ready = refresh().catch(e => {
    $('ctx-course').replaceChildren(el('option', '课程读取失败'));
    if (typeof error === 'function') error(e.message);
    throw e;
  });
})();
