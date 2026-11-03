// 막힌 기사를 사용자 브라우저의 백그라운드 탭으로 하나씩 열어 본문을 읽고, 시장 분석 서버로 보낸 뒤 탭을 닫습니다.
// 매체에 부담을 주거나 차단당하지 않도록 한 번에 한 건씩, 간격을 두고, 최대 MAX_JOBS건만 처리합니다.
const GOOGLE_ARTICLE = "https://news.google.com/rss/articles/";
const ALLOWED_SERVERS = ["http://127.0.0.1:9000", "http://localhost:9000"];
const MAX_JOBS = 20;
const GAP_MS = 2500;
const LOAD_TIMEOUT_MS = 25000;
const RENDER_POLL_MS = 1000;
const RENDER_MAX_POLLS = 8; // 본문 문단이 다 그려질 때까지 최대 8초 기다립니다.
const MIN_TEXT_CHARS = 600;
const MAX_HTML_CHARS = 1_900_000;
const CHALLENGE = /just a moment|attention required|access denied|are you a robot|verify you are human|captcha|보안 확인|잠시만 기다리십시오/i;

let queue = [];
let running = false;
let cancelled = false;
const watchedClicks = new Map(); // Google 뉴스 링크 → 사용자가 막 누른 기사 정보
const watchedTabs = new Map(); // 탭 ID → 사용자가 직접 연 기사 정보

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const isGoogle = url => !url || new URL(url).hostname === "news.google.com";

function serverOf(sender) {
  const origin = sender.tab?.url ? new URL(sender.tab.url).origin : "";
  return ALLOWED_SERVERS.includes(origin) ? origin : null;
}

chrome.runtime.onMessage.addListener((message, sender) => {
  const server = serverOf(sender);
  if (!server) return;
  const pageTabId = sender.tab.id;
  if (message.type === "summarize") {
    const known = new Set(queue.map(job => job.url));
    for (const job of message.jobs) {
      if (queue.length >= MAX_JOBS) break;
      if (typeof job?.url !== "string" || !job.url.startsWith(GOOGLE_ARTICLE) || known.has(job.url)) continue;
      const openUrl = /^https?:\/\//.test(job.open_url || "") ? job.open_url : job.url;
      queue.push({ url: job.url, subject: String(job.subject || "").slice(0, 80), openUrl, server, pageTabId });
      known.add(job.url);
    }
    run();
  } else if (message.type === "cancel") {
    queue = [];
    cancelled = true;
  } else if (message.type === "watch" && typeof message.url === "string" && message.url.startsWith(GOOGLE_ARTICLE)) {
    watchedClicks.set(message.url, { url: message.url, subject: String(message.subject || "").slice(0, 80), server, pageTabId, at: Date.now() });
  }
});

function notify(job, data) {
  chrome.tabs.sendMessage(job.pageTabId, { url: job.url, ...data }).catch(() => {});
}

function snapshot(max) {
  // 탭 안에서 실행됩니다. 문단 글자 수로 본문이 다 그려졌는지 판단하고, Cloudflare 등 확인 화면은 구조로 알아봅니다.
  const text = [...document.querySelectorAll("p")].reduce((sum, p) => sum + p.innerText.trim().length, 0);
  const challenge = Boolean(document.querySelector(
    '#challenge-form, #challenge-running, #cf-challenge-running, [id^="cf-chl"], script[src*="challenge-platform"], iframe[src*="captcha"]'
  ));
  return { url: location.href, title: document.title, text, challenge, html: document.documentElement.outerHTML.slice(0, max) };
}

async function readPage(tabId) {
  let page;
  for (let poll = 0; poll < RENDER_MAX_POLLS; poll++) {
    await sleep(RENDER_POLL_MS);
    [{ result: page }] = await chrome.scripting.executeScript({ target: { tabId }, func: snapshot, args: [MAX_HTML_CHARS] });
    if (page.text >= MIN_TEXT_CHARS || page.challenge) break;
  }
  return page;
}

async function submit(job, page) {
  const response = await fetch(`${job.server}/api/v1/article-summary/from-browser`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Toss-Extension": "1" },
    body: JSON.stringify({ url: job.url, subject: job.subject, page_url: page.url, html: page.html }),
  });
  if (!response.ok) throw new Error(`서버 응답 ${response.status}`);
  return response.json();
}

// 읽은 페이지가 사람 확인 화면이면 서버로 보내지 않습니다(보내면 실패 결과가 캐시에 남습니다).
async function handlePage(job, page) {
  if (page.challenge || CHALLENGE.test(page.title)) {
    return { ok: false, challenge: true, reason: "매체가 사람 확인 화면을 띄웠습니다. 기사를 직접 열어 확인을 통과해 주세요." };
  }
  if (page.html.length < 3000 && !page.text) {
    return { ok: false, reason: "매체가 빈 페이지를 보냈습니다. 자동으로 연 탭을 막는 매체일 수 있어, 기사를 직접 열어 주세요." };
  }
  const result = await submit(job, page);
  return { ok: Boolean(result.ok), reason: result.reason || "" };
}

function waitForArticle(tabId) {
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      error ? reject(error) : resolve();
    };
    // Google 뉴스 중계 페이지는 언론사 페이지로 넘어갈 때까지 기다립니다.
    const check = tab => { if (tab.status === "complete" && !isGoogle(tab.url)) finish(); };
    const listener = (id, _info, tab) => { if (id === tabId) check(tab); };
    const timer = setTimeout(() => finish(new Error("기사 페이지가 25초 안에 열리지 않았습니다.")), LOAD_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(check, () => finish(new Error("탭이 닫혔습니다.")));
  });
}

async function readInBackground(job) {
  const tab = await chrome.tabs.create({ url: job.openUrl, active: false });
  try {
    await waitForArticle(tab.id);
    return await readPage(tab.id);
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function run() {
  if (running) return;
  running = true;
  cancelled = false;
  try {
    while (queue.length && !cancelled) {
      const job = queue.shift();
      notify(job, { state: "reading", remaining: queue.length });
      let outcome;
      try {
        outcome = await handlePage(job, await readInBackground(job));
      } catch (error) {
        outcome = { ok: false, reason: error.message };
      }
      notify(job, { state: "done", ...outcome, remaining: queue.length });
      if (queue.length) await sleep(GAP_MS);
    }
  } finally {
    running = false;
  }
}

// 사용자가 직접 연 기사 탭: 다 열리면 본문을 읽어 보냅니다. 탭은 닫지 않습니다.
chrome.tabs.onCreated.addListener(tab => {
  const url = tab.pendingUrl || tab.url;
  const click = watchedClicks.get(url);
  if (!click) return;
  watchedClicks.delete(url);
  if (Date.now() - click.at < 10000) watchedTabs.set(tab.id, click);
});

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  const job = watchedTabs.get(tabId);
  if (!job || info.status !== "complete" || isGoogle(tab.url)) return;
  watchedTabs.delete(tabId);
  notify(job, { state: "reading" });
  try {
    const outcome = await handlePage(job, await readPage(tabId));
    // 사람 확인 화면이면 사용자가 통과한 뒤 페이지가 다시 열릴 때 한 번 더 읽습니다.
    if (outcome.challenge) watchedTabs.set(tabId, job);
    notify(job, { state: "done", ...outcome });
  } catch (error) {
    notify(job, { state: "done", ok: false, reason: error.message });
  }
});

chrome.tabs.onRemoved.addListener(tabId => watchedTabs.delete(tabId));
