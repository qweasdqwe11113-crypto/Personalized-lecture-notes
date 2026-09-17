const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = Number(process.env.PORT || 3000);
const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, 'public');
const DATA_DIR = path.join(ROOT, 'data');
const DB_FILE = path.join(DATA_DIR, 'knowledge.json');

const seedDocuments = [
  {
    id: 'seed-physics-1',
    name: '高中物理必修一（示例节选）',
    type: '教材',
    source: '系统示例',
    createdAt: new Date().toISOString(),
    content: '牛顿第二定律：物体加速度的大小跟作用力成正比，跟物体质量成反比，加速度方向与合外力方向相同。表达式为 F=ma。',
    chunks: ['牛顿第二定律：物体加速度的大小跟作用力成正比，跟物体质量成反比。', '表达式为 F=ma，加速度方向与合外力方向相同。']
  },
  {
    id: 'seed-physics-2',
    name: '课堂教学目标（示例）',
    type: '教案',
    source: '系统示例',
    createdAt: new Date().toISOString(),
    content: '学生能够理解合外力、质量和加速度的关系；能够运用 F=ma 解决简单直线运动问题；能够规范写出单位和计算过程。',
    chunks: ['理解合外力、质量和加速度的关系。', '运用 F=ma 解决简单直线运动问题，并规范写出单位和计算过程。']
  }
];

function ensureDatabase() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(DB_FILE)) {
    fs.writeFileSync(DB_FILE, JSON.stringify(seedDocuments, null, 2), 'utf8');
  }
}

function readDocuments() {
  ensureDatabase();
  try {
    return JSON.parse(fs.readFileSync(DB_FILE, 'utf8'));
  } catch {
    return [...seedDocuments];
  }
}

function writeDocuments(documents) {
  ensureDatabase();
  fs.writeFileSync(DB_FILE, JSON.stringify(documents, null, 2), 'utf8');
}

function splitText(text) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return [];
  const sentences = clean.split(/(?<=[。！？；.!?;])/).filter(Boolean);
  const chunks = [];
  let current = '';
  for (const sentence of sentences) {
    if ((current + sentence).length > 220 && current) {
      chunks.push(current.trim());
      current = sentence;
    } else {
      current += sentence;
    }
  }
  if (current.trim()) chunks.push(current.trim());
  return chunks.slice(0, 50);
}

function scoreChunk(query, chunk) {
  const terms = [...new Set(String(query).toLowerCase().split(/[\s，。？！、：:；;]+/).filter(term => term.length > 1))];
  const lower = chunk.toLowerCase();
  return terms.reduce((score, term) => score + (lower.includes(term) ? term.length : 0), 0);
}

function retrieve(query, limit = 5) {
  const docs = readDocuments();
  const results = [];
  for (const doc of docs) {
    for (const chunk of doc.chunks || splitText(doc.content)) {
      results.push({
        documentId: doc.id,
        documentName: doc.name,
        source: doc.source,
        text: chunk,
        score: scoreChunk(query, chunk)
      });
    }
  }
  return results.sort((a, b) => b.score - a.score).slice(0, limit);
}

function detectTopic(message) {
  const known = ['牛顿第二定律', '勾股定理', '光合作用', '函数', '力与运动'];
  return known.find(item => message.includes(item)) || message.replace(/请|帮我|生成|一份|讲义|出题|题目|根据|知识库/g, '').trim().slice(0, 24) || '本节课知识点';
}

function generateHandout(topic, profile, evidence) {
  const level = profile?.level || '中等';
  const goal = level === '基础' ? '掌握核心概念，并能完成基础计算' : level === '提高' ? '建立知识联系，并解决综合情境问题' : '理解概念并能完成典型应用';
  const references = evidence.map((item, index) => `[${index + 1}] ${item.documentName}`).join('；');
  const core = evidence.map(item => item.text).slice(0, 3).join('\n');
  return {
    title: `${topic} · 个性化课堂讲义`,
    markdown: `### 学习目标\n面向${level}水平学生：${goal}。\n\n### 核心知识\n${core || `围绕“${topic}”梳理定义、规律与基本方法。`}\n\n### 教学建议\n先用生活情境引入概念，再通过一个示例完成建模，最后安排分层练习。${profile?.weakness ? `重点关注学生薄弱项：${profile.weakness}。` : ''}\n\n### 课堂小结\n请学生用自己的话复述核心规律，并说明使用条件。\n\n**资料依据：** ${references || '当前知识库暂无匹配资料，请先上传材料。'}`,
    sources: evidence
  };
}

function generateQuestions(topic, profile, evidence) {
  const level = profile?.level || '中等';
  const base = evidence[0]?.text || `${topic}的基本概念与应用`;
  return {
    title: `${topic} · ${level}难度练习`,
    markdown: `### 1. 单项选择题（3分）\n关于“${topic}”，下列说法最符合知识库材料的是（ ）\n\nA. 与材料无关的表述\nB. ${base.slice(0, 58)}\nC. 所有条件下结论都相同\nD. 无法从任何资料判断\n\n**答案：B**\n\n### 2. 简答题（5分）\n请结合一个具体情境，说明“${topic}”的核心含义和适用条件。\n\n**评分点：** 核心概念2分；条件说明2分；表达规范1分。\n\n### 3. 个性化巩固题（7分）\n${profile?.weakness ? `针对“${profile.weakness}”这一薄弱项，` : ''}设计一个与“${topic}”相关的问题，并给出完整解题过程。\n\n**参考要求：** 条件完整、方法正确、过程清晰。`,
    sources: evidence
  };
}

function buildChatResponse(body) {
  const message = String(body.message || '').trim();
  const profile = body.profile || {};
  const topic = detectTopic(message);
  const evidence = retrieve(`${topic} ${message}`);
  if (/出题|试题|练习|组卷/.test(message)) return { intent: 'generate_questions', ...generateQuestions(topic, profile, evidence) };
  if (/讲义|教案|备课|教学设计/.test(message)) return { intent: 'generate_handout', ...generateHandout(topic, profile, evidence) };
  if (/知识库|资料|文档/.test(message)) {
    const docs = readDocuments();
    return {
      intent: 'knowledge_overview',
      title: '知识库概览',
      markdown: `当前知识库共有 **${docs.length}** 份资料、**${docs.reduce((sum, doc) => sum + (doc.chunks?.length || 0), 0)}** 个知识片段。你可以让我基于这些资料生成讲义或题目。`,
      sources: evidence
    };
  }
  return {
    intent: 'qa',
    title: '知识库回答',
    markdown: evidence.length ? `根据知识库中最相关的资料：\n\n${evidence.slice(0, 3).map(item => `- ${item.text}`).join('\n')}\n\n你还可以继续说“生成讲义”或“出5道题”。` : '当前没有检索到相关资料，请先上传教学材料或使用联网补充功能。',
    sources: evidence
  };
}

function sendJson(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', chunk => {
      raw += chunk;
      if (raw.length > 5 * 1024 * 1024) reject(new Error('请求内容过大'));
    });
    req.on('end', () => {
      try { resolve(raw ? JSON.parse(raw) : {}); } catch { reject(new Error('JSON 格式错误')); }
    });
    req.on('error', reject);
  });
}

async function searchWikipedia(topic) {
  const url = `https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(topic)}&format=json&origin=*&srlimit=3`;
  const response = await fetch(url, { signal: AbortSignal.timeout(5000), headers: { 'User-Agent': 'TeachingRAGDemo/1.0' } });
  if (!response.ok) throw new Error('联网检索失败');
  const json = await response.json();
  return (json.query?.search || []).map(item => ({
    title: item.title,
    url: `https://zh.wikipedia.org/wiki/${encodeURIComponent(item.title)}`,
    snippet: item.snippet.replace(/<[^>]+>/g, '')
  }));
}

async function handleApi(req, res, pathname) {
  if (req.method === 'GET' && pathname === '/api/health') return sendJson(res, 200, { ok: true, service: 'VLM + RAG Teaching Demo' });
  if (req.method === 'GET' && pathname === '/api/documents') return sendJson(res, 200, { documents: readDocuments() });

  if (req.method === 'POST' && pathname === '/api/documents') {
    const body = await parseBody(req);
    if (!body.name) return sendJson(res, 400, { error: '缺少文件名' });
    const content = String(body.content || `${body.name}：该文件已上传。演示版尚未启用 PDF/OCR 内容解析。`).slice(0, 200000);
    const document = {
      id: crypto.randomUUID(),
      name: body.name,
      type: body.type || '上传资料',
      source: '教师上传',
      size: body.size || 0,
      createdAt: new Date().toISOString(),
      content,
      chunks: splitText(content)
    };
    const documents = readDocuments();
    documents.unshift(document);
    writeDocuments(documents);
    return sendJson(res, 201, { document });
  }

  if (req.method === 'DELETE' && pathname.startsWith('/api/documents/')) {
    const id = decodeURIComponent(pathname.split('/').pop());
    const documents = readDocuments();
    const next = documents.filter(doc => doc.id !== id);
    writeDocuments(next);
    return sendJson(res, 200, { deleted: documents.length - next.length });
  }

  if (req.method === 'POST' && pathname === '/api/chat') {
    const body = await parseBody(req);
    if (!body.message?.trim()) return sendJson(res, 400, { error: '请输入消息' });
    return sendJson(res, 200, buildChatResponse(body));
  }

  if (req.method === 'POST' && pathname === '/api/web-search') {
    const body = await parseBody(req);
    const topic = String(body.topic || '人工智能教育').trim();
    let results;
    let online = true;
    try {
      results = await searchWikipedia(topic);
      if (!results.length) throw new Error('没有结果');
    } catch {
      online = false;
      results = [
        { title: `${topic}课程概念（演示资料）`, url: 'demo://fallback/1', snippet: `${topic}的核心概念、适用范围与典型教学案例。` },
        { title: `${topic}课堂练习（演示资料）`, url: 'demo://fallback/2', snippet: `围绕${topic}设计基础理解、迁移应用和综合分析三个层次的练习。` }
      ];
    }
    const documents = readDocuments();
    const imported = results.map(item => ({
      id: crypto.randomUUID(),
      name: item.title,
      type: online ? '联网资料' : '演示资料',
      source: item.url,
      createdAt: new Date().toISOString(),
      content: item.snippet,
      chunks: splitText(item.snippet)
    }));
    writeDocuments([...imported, ...documents]);
    return sendJson(res, 200, { online, results, imported: imported.length });
  }

  return sendJson(res, 404, { error: '接口不存在' });
}

const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8'
};

function serveStatic(res, pathname) {
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\//, '');
  const file = path.resolve(PUBLIC_DIR, relative);
  if (!file.startsWith(path.resolve(PUBLIC_DIR))) {
    res.writeHead(403); return res.end('Forbidden');
  }
  fs.readFile(file, (error, data) => {
    if (error) { res.writeHead(404); return res.end('Not Found'); }
    res.writeHead(200, { 'Content-Type': mimeTypes[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
}

ensureDatabase();
const server = http.createServer(async (req, res) => {
  const pathname = new URL(req.url, `http://${req.headers.host || 'localhost'}`).pathname;
  try {
    if (pathname.startsWith('/api/')) return await handleApi(req, res, pathname);
    serveStatic(res, pathname);
  } catch (error) {
    sendJson(res, 500, { error: error.message || '服务器错误' });
  }
});

server.listen(PORT, () => {
  console.log(`VLM + RAG 教学原型已启动：http://localhost:${PORT}`);
});
