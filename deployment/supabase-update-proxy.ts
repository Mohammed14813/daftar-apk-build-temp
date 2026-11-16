// Supabase Edge Function route: /update/*
// Secret: CLOUDFLARE_UPDATE_URL = https://<your-worker>.workers.dev
Deno.serve(async (req) => {
  const incoming = new URL(req.url);
  const base = (Deno.env.get("CLOUDFLARE_UPDATE_URL") || "").replace(/\/+$/, "");
  if (!base) return new Response(JSON.stringify({ok:false,error:"CLOUDFLARE_UPDATE_URL is not configured"}), {status:503,headers:{"content-type":"application/json"}});
  const suffix = incoming.pathname.split("/update")[1] || "/info";
  const target = base + "/update" + suffix + incoming.search;
  const r = await fetch(target, {method:req.method, headers:{"accept":req.headers.get("accept") || "*/*"}});
  const headers = new Headers(r.headers);
  headers.set("access-control-allow-origin", "*");
  headers.set("cache-control", "no-store");
  return new Response(r.body, {status:r.status, headers});
});
