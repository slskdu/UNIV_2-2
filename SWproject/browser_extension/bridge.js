// 시장 분석 화면(127.0.0.1:9000)에서만 실행됩니다. 화면과 확장 프로그램 사이에서 메시지를 전달합니다.
const PREFIX = "toss-ext:";
const GOOGLE_ARTICLE = "https://news.google.com/rss/articles/";

// 화면이 이 값을 보고 "막힌 기사 브라우저로 요약" 버튼을 보여 줍니다.
document.documentElement.dataset.tossExtension = chrome.runtime.getManifest().version;

window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin) return;
  const data = event.data;
  if (data?.type === `${PREFIX}summarize` && Array.isArray(data.jobs)) {
    chrome.runtime.sendMessage({ type: "summarize", jobs: data.jobs });
  } else if (data?.type === `${PREFIX}cancel`) {
    chrome.runtime.sendMessage({ type: "cancel" });
  }
});

// 사용자가 요약되지 않은 기사의 제목을 눌러 원문을 열면, 그 탭이 다 열린 뒤 본문을 읽도록 알려 둡니다.
document.addEventListener("click", event => {
  const link = event.target.closest?.(".news-list li[data-url] a");
  const item = link?.closest("li");
  if (!item || !item.dataset.url.startsWith(GOOGLE_ARTICLE) || item.dataset.summary === "ok") return;
  chrome.runtime.sendMessage({ type: "watch", url: item.dataset.url, subject: item.dataset.subject || "" });
}, true);

chrome.runtime.onMessage.addListener(message => {
  window.postMessage({ ...message, type: `${PREFIX}progress` }, location.origin);
});
