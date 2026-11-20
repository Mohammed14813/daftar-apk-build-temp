// Cloudflare Worker: app -> Supabase Edge Function -> this Worker -> GitHub
// Secrets: GITHUB_TOKEN, optional GITHUB_REPO (default Mohammed14813/Integrated-Accountant-Updates)
const REPO = (env) => env.GITHUB_REPO || "Mohammed14813/Integrated-Accountant-Updates";
const common = { "access-control-allow-origin": "*", "cache-control": "no-store" };
function reply(body, status=200, headers={}) { return new Response(body, {status, headers: {...common, ...headers}}); }
async function gh(path, env, accept="application/vnd.github+json") {
  const r = await fetch(`https://api.github.com/repos/${REPO(env)}/${path}`, {headers: {
    accept, "user-agent": "Daftar-Android-Update-Proxy",
    ...(env.GITHUB_TOKEN ? {authorization: `Bearer ${env.GITHUB_TOKEN}`} : {})
  }});
  return r;
}
export default { async fetch(request, env) {
  const u = new URL(request.url);
  if (request.method === "OPTIONS") return reply(null, 204, {"access-control-allow-methods":"GET,OPTIONS","access-control-allow-headers":"content-type"});
  if (request.method !== "GET") return reply(JSON.stringify({ok:false,error:"method_not_allowed"}), 405, {"content-type":"application/json"});
  if (u.pathname === "/health") return reply(JSON.stringify({ok:true}), 200, {"content-type":"application/json"});
  if (u.pathname === "/update/info") {
    const r = await gh("commits?path=app-bundle.zip&sha=main&per_page=1", env);
    if (!r.ok) return reply(JSON.stringify({ok:false,error:`github_${r.status}`}), 502, {"content-type":"application/json"});
    const rows = await r.json();
    return reply(JSON.stringify({ok:true, sha: rows?.[0]?.sha || ""}), 200, {"content-type":"application/json"});
  }
  if (u.pathname === "/update/bundle") {
    const r = await gh("contents/app-bundle.zip?ref=main", env, "application/vnd.github.raw");
    if (!r.ok) return reply(await r.text(), 502, {"content-type":"application/json"});
    return new Response(r.body, {status:200, headers:{...common,"content-type":"application/zip","content-disposition":"attachment; filename=app-bundle.zip"}});
  }
  return reply(JSON.stringify({ok:false,error:"not_found"}), 404, {"content-type":"application/json"});
} };
