/* List transitions: rows that are added fade in, removed rows fold away and the others slide into
   place. AutoAnimate (vendor/auto-animate, MIT) watches each list; the scripts redraw these lists
   with ui.morph so unchanged rows stay. AutoAnimate turns itself off under "reduce motion". */
import autoAnimate from './vendor/auto-animate/index.mjs';

const LISTS = ['jobs-list', 'history', 'upload-list', 'student-roster', 'quiz-history', 'generation-runs'];
const options = {duration: 200, easing: 'cubic-bezier(.2,.8,.2,1)'};
for (const id of LISTS) {
  const list = document.getElementById(id);
  if (list) autoAnimate(list, options);
}
