/* 讲义大纲悬浮目录（多级）
 * - 自动提取 H1~H3 标题生成多级目录
 * - 点击悬浮球展开右侧抽屉，支持当前阅读章节高亮
 */
(() => {
  'use strict';

  const LECTURE_ID = 'lecture';
  let isOpen = false;

  // 构建 UI 骨架
  function buildUI() {
    if (document.getElementById('toc-toggle')) return;

    // 悬浮球
    const toggle = document.createElement('button');
    toggle.id = 'toc-toggle';
    toggle.className = 'toc-toggle-btn hidden';
    toggle.innerHTML = '☰'; // 可以使用 SVG 或图标
    toggle.title = '查看目录';
    toggle.onclick = () => toggleSidebar(true);
    document.body.appendChild(toggle);

    // 遮罩
    const overlay = document.createElement('div');
    overlay.id = 'toc-overlay';
    overlay.className = 'toc-overlay';
    overlay.onclick = () => toggleSidebar(false);
    document.body.appendChild(overlay);

    // 侧边栏
    const sidebar = document.createElement('aside');
    sidebar.id = 'toc-sidebar';
    sidebar.className = 'toc-sidebar';
    sidebar.innerHTML = `
      <div class="toc-header">
        <span>讲义目录</span>
        <button class="toc-close-btn" aria-label="关闭">×</button>
      </div>
      <div class="toc-list" id="toc-list"></div>
    `;
    sidebar.querySelector('.toc-close-btn').onclick = () => toggleSidebar(false);
    document.body.appendChild(sidebar);
  }

  // 控制展开/收起
  function toggleSidebar(open) {
    isOpen = open;
    document.getElementById('toc-sidebar')?.classList.toggle('open', open);
    document.getElementById('toc-overlay')?.classList.toggle('open', open);
  }

  // 扫描标题并生成目录
  function annotate(root) {
    buildUI();
    const toggleBtn = document.getElementById('toc-toggle');
    const list = document.getElementById('toc-list');
    if (!root || !list || !toggleBtn) return;

    // 筛选 H1, H2, H3, H4，且只找带 data-section-title 的
    const heads = [...root.querySelectorAll('h1, h2, h3, h4')]
      .filter(h => h.hasAttribute('data-section-title'));

    list.replaceChildren();

    if (heads.length === 0) {
      toggleBtn.classList.add('hidden');
      toggleSidebar(false);
      return;
    }
    toggleBtn.classList.remove('hidden');

    heads.forEach((h, i) => {
      const id = `toc-heading-${i}`;
      h.id = id;

      const level = parseInt(h.tagName.slice(1), 10);
      const label = (h.dataset.sectionTitle || h.textContent || '').trim();

      const a = document.createElement('a');
      a.className = `toc-item toc-level-${level}`;
      a.href = `#${id}`;
      a.textContent = label;
      a.title = label;
      
      a.onclick = (e) => {
        e.preventDefault();
        h.scrollIntoView({ behavior: 'smooth', block: 'start' });
        toggleSidebar(false); // 点击后关闭侧边栏
      };

      list.appendChild(a);
    });

    setupScrollSpy(heads, list);
  }

  // 滚动高亮当前章节
  let spyObserver = null;
  function setupScrollSpy(heads, list) {
    if (spyObserver) spyObserver.disconnect();

    const options = {
      root: null,
      rootMargin: '0px 0px -60% 0px', // 视口上部 40% 区域触发
      threshold: 0
    };

    spyObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          // 移除旧高亮
          list.querySelectorAll('.toc-item').forEach(el => el.classList.remove('active'));
          // 添加新高亮
          const idx = heads.indexOf(entry.target);
          if (idx !== -1) {
            const activeLink = list.children[idx];
            if (activeLink) {
              activeLink.classList.add('active');
              activeLink.scrollIntoView({ block: 'nearest' }); // 确保目录内也可见
            }
          }
        }
      });
    }, options);

    heads.forEach(h => spyObserver.observe(h));
  }

  // 暴露给 app.js 调用
  window.annotateTopicNav = annotate;
  window.clearTopicNav = () => {
    document.getElementById('toc-toggle')?.classList.add('hidden');
    toggleSidebar(false);
    if (spyObserver) spyObserver.disconnect();
  };

  // 监听讲义版本切换
  window.addEventListener('lecture-version-changed', () => {
    const root = document.getElementById(LECTURE_ID);
    if (!root || root.hidden || root.children.length === 0) {
      window.clearTopicNav();
    } else {
      annotate(root);
    }
  });
})();