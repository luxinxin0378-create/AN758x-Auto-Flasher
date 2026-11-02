# -*- coding: utf-8 -*-
"""前端单页（被 flasher.py 读取并整体作为 / 返回）。"""

UI_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AN758x 光猫免拆刷机工具</title>
<style>
:root{--bg:#f4f6f9;--card:#fff;--ink:#1c2733;--mut:#66788c;--line:#dfe6ee;
--pri:#185fa5;--pri-2:#0c447c;--ok:#3b6d11;--warn:#854f0b;--err:#a32d2d;--mono:Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;align-items:baseline;gap:12px;margin-bottom:6px}
h1{font-size:19px;margin:0}
header .sub{color:var(--mut);font-size:12.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin:14px 0}
.card h2{font-size:14.5px;margin:0 0 10px;color:var(--pri-2)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
label{font-size:13px;color:var(--mut)}
input[type=text]{border:1px solid var(--line);border-radius:7px;padding:7px 9px;
font-size:13.5px;min-width:220px;flex:1}
select{border:1px solid var(--line);border-radius:7px;padding:7px 9px;font-size:13.5px;min-width:260px}
button{border:0;border-radius:8px;padding:8px 16px;font-size:13.5px;cursor:pointer;
background:var(--pri);color:#fff}
button.ghost{background:#eef3f8;color:var(--pri-2);border:1px solid var(--line)}
button.danger{background:var(--err)}
button:disabled{opacity:.45;cursor:not-allowed}
.modes{display:flex;gap:8px;flex-wrap:wrap}
.mode{border:1.5px solid var(--line);border-radius:9px;padding:10px 14px;cursor:pointer;
flex:1;min-width:200px;background:#fbfdff}
.mode.sel{border-color:var(--pri);background:#eaf2fa}
.mode b{display:block;font-size:13.5px}
.mode span{font-size:12px;color:var(--mut)}
.note{background:#f6f2e7;border:1px solid #eadfc4;color:var(--warn);border-radius:8px;
padding:9px 12px;font-size:12.5px;margin:10px 0;white-space:pre-line}
.files .f{display:grid;grid-template-columns:110px 1fr auto;gap:8px;align-items:center;
padding:6px 0;border-bottom:1px dashed var(--line);font-size:13px}
.files .f:last-child{border:0}
.files .f b{font-size:12.5px;color:var(--mut);font-weight:500}
.ok-tag{color:var(--ok);font-size:12px}
.miss{color:var(--err);font-size:12px}
#panel{display:none}
#steps{margin:0;padding:0;list-style:none}
#steps li{display:flex;gap:10px;padding:5px 0;font-size:13.5px;align-items:baseline}
.st{width:18px;text-align:center;font-weight:700}
li[data-st=pending] .st{color:#b9c3cf}
li[data-st=running] .st{color:var(--pri)}
li[data-st=running] .nm::after{content:"…"}
li[data-st=done] .st{color:var(--ok)}
li[data-st=confirm] .st{color:var(--warn)}
li[data-st=error] .st{color:var(--err)}
li .nm{min-width:200px}
li .dt{color:var(--mut);font-size:12px}
#logs{background:#101822;color:#c8e0f5;font:12px/1.5 var(--mono);border-radius:8px;
padding:12px;height:240px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}
#logs .err{color:#ff9b9b}
#modal{position:fixed;inset:0;background:rgba(16,24,34,.55);display:none;
align-items:center;justify-content:center;z-index:50}
#modal .box{background:#fff;border-radius:12px;max-width:520px;width:92%;
padding:22px;box-shadow:0 18px 50px rgba(0,0,0,.35)}
#modal h3{margin:0 0 8px;color:var(--err);font-size:15.5px}
#modal p{white-space:pre-line;font-size:13.5px;margin:0 0 18px}
footer{color:var(--mut);font-size:11.5px;text-align:center;margin-top:24px}
hr{border:0;border-top:1px solid var(--line);margin:14px 0}
.tag{display:inline-block;background:#eaf2fa;color:var(--pri-2);border-radius:5px;
padding:1px 8px;font-size:11.5px;margin-right:6px}
#devinfo{font-size:12.5px;color:var(--mut);margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>AN758x 光猫免拆刷机 · 备份 · 还原</h1>
  <span class="sub">多机型 · 基于 stock2ubi + U-Boot Web API</span>
</header>

<div class="card">
  <h2>1 · 选择机型</h2>
  <div class="row">
    <select id="dev"></select>
  </div>
  <div id="devinfo"></div>
</div>

<div class="card">
  <h2>2 · 选择模式</h2>
  <div class="modes">
    <div class="mode sel" data-mode="full"><b>一键刷机</b><span>备份 → 刷引导 → 刷系统，全程免拆</span></div>
    <div class="mode" data-mode="backup"><b>仅备份</b><span>只做全分区备份与板级数据转换</span></div>
    <div class="mode" data-mode="restore"><b>从备份还原</b><span>整片 NAND 恢复原厂（需 U-Boot）</span></div>
  </div>
</div>

<div class="card" id="conncard">
  <h2>3 · 连接参数</h2>
  <div class="row" style="margin-bottom:8px">
    <label>光猫 IP</label><input type=text id=ip value="192.168.1.1">
    <label>MAC</label><input type=text id=mac placeholder="70D51E046CEC（烽火动态密码需要）">
    <button class=ghost id=probemac>自动读取</button>
  </div>
  <div class="row" style="margin-bottom:8px">
    <label>telnet 用户</label><input type=text id=tuser>
    <label>密码</label><input type=text id=tpass placeholder="留空=按机型规则推导">
  </div>
  <div class="row">
    <label>su 密码</label><input type=text id=spass placeholder="留空=按机型规则推导">
  </div>
  <div class="note" id="pwNote"></div>
</div>

<div class="card files" id="filecard">
  <h2>4 · 固件文件</h2>
  <div class="row" style="margin-bottom:10px">
    <label>固件目录</label><input type=text id=fdir placeholder="留空=下载到 exe 旁 firmware\ 目录">
    <button class=ghost id=scan>扫描匹配</button>
    <button class=ghost id=fetchfw>使用内置引导包</button>
    <button class=ghost id=fetchfwOnline title="强制从 uboot-an758x Releases 下载最新版">联网更新</button>
  </div>
  <div class="note" id=fwTag style="margin-bottom:8px"></div>
  <div class="note" id=fwHint style="display:none"></div>
  <div class="f"><b>BL2 引导</b><input type=text id=bl2 placeholder="*-firstblock.bin / *-preloader.bin"><span id=bl2s></span></div>
  <div class="f"><b>U-Boot FIP</b><input type=text id=fip placeholder="*-bl31-u-boot.fip"><span id=fips></span></div>
  <div class="f"><b>系统固件</b><input type=text id=sys placeholder="*-sysupgrade.itb / .bin"><span id=syss></span></div>
  <div class="f" id=restorerow style="display:none"><b>备份目录</b><input type=text id=bdir placeholder="backups\hg5585f-cu_20261102_..."><span></span></div>
  <hr>
  <div class="row">
    <label>stock2ubi 工具</label><span id=toolst></span>
    <span class=tag style="margin:0 4px">exe 版内置 v0.1.14</span>
    <button class=ghost id=uploadtool>手动上传</button>
    <input type=file id=toolfile style="display:none">
  </div>
</div>

<div class="card">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">5 · 执行</h2>
    <div class="row">
      <button id=start>开始</button>
      <button class=danger id=abort style="display:none">中止</button>
    </div>
  </div>
  <div id="panel">
    <ul id="steps"></ul>
    <div id="logs"></div>
  </div>
</div>

<footer>刷机有风险 · factory 分区丢失不可再生 · 请勿使用他人设备身份数据<br>
工具开源依赖：an758x-stock2ubi / uboot-an758x / ponwrt（pbs05）、fiberhome-factory</footer>
</div>

<div id="modal"><div class="box">
  <h3>⚠ 危险操作确认</h3>
  <p id="mtext"></p>
  <div class="row" style="justify-content:flex-end">
    <button class=ghost id=mdeny>取消（中止）</button>
    <button id=mok>确认执行</button>
  </div>
</div></div>

<script>
let DEVICES=[],DEV=null,MODE="full",STATE=null,TIMER=null,BUNDLED_TAG="",BUNDLED_N=0;

const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function jget(u){const r=await fetch(u);return r.json()}
async function jpost(u,o){const r=await fetch(u,{method:"POST",
 headers:{"Content-Type":"application/json"},body:JSON.stringify(o||{})});return r.json()}

async function init(){
  const d=await jget("/api/devices");DEVICES=d.devices;
  BUNDLED_TAG=d.bundled_tag||"";BUNDLED_N=d.bundled_count||0;
  $("dev").innerHTML=DEVICES.map(x=>`<option value="${x.key}">${esc(x.name)}</option>`).join("");
  $("fwTag").textContent=BUNDLED_TAG?`内置 AN758x 引导包 ${esc(BUNDLED_TAG)}（${BUNDLED_N} 个文件，离线可用）`:"未内置引导包，需联网拉取";
  const saved=localStorage.getItem("pf_dev"); if(saved) $("dev").value=saved;
  $("fdir").value=localStorage.getItem("pf_dir")||"";
  pick(); checkTool();
}
function pick(){
  DEV=DEVICES.find(x=>x.key===$("dev").value)||DEVICES[0];
  localStorage.setItem("pf_dev",DEV.key);
  $("devinfo").innerHTML=`<span class=tag>${esc(DEV.vendor)}</span>`+
   `<span class=tag>板级数据：${esc(DEV.board_data)}${DEV.board_convert==="fiberhome"?"（自动转换）":""}</span>`+
   `<span class=tag>恢复页：${esc(DEV.uboot_ip)}</span>`+
   (DEV.bundled?`<span class=tag style="border-color:#15803d;color:#15803d">引导包已内置</span>`
    :`<span class=tag style="border-color:#c2410c;color:#c2410c">引导包需自备</span>`)+
   `<br>${esc(DEV.note)}`+
   `<br>telnet：用户 <code>${esc($("tuser").value||"?")}</code>（页面下方可覆盖）`;
  const noBundled=!DEV.bundled;
  $("fwHint").style.display=noBundled?"block":"none";
  $("fwHint").innerHTML=noBundled
   ?`<b>该机型的引导包未内置</b>：uboot-an758x 源码里有 defconfig
     <code>${esc(DEV.uboot_defconfig||"?")}</code>，但 Releases 未发布预编译包
     （CI 只上传到 Actions artifacts）。请到
     <code>github.com/pbs05/uboot-an758x/actions</code> 登录后下载
     <code>an758x-${esc(DEV.key)}</code> 产物，或自行编译
     <code>./scripts/build-an758x.sh ${esc(DEV.key)}</code>，
     放入固件目录后点「扫描匹配」。`
   :"";
  $("pwNote").textContent=DEV.telnet_enable==="fhtool"
   ?"该机型可自动读取 MAC 并推导密码（也可手工覆盖）。若 8080 的 fh_tool RPC 可用，可先自动开 telnet。"
   :"该机型请确保 telnet 已开启，账号密码按当地固件实际填写（默认值仅为常见参考）。";
}
$("dev").onchange=pick;

document.querySelectorAll(".mode").forEach(m=>m.onclick=()=>{
  document.querySelectorAll(".mode").forEach(x=>x.classList.remove("sel"));
  m.classList.add("sel");MODE=m.dataset.mode;
  $("restorerow").style.display=MODE==="restore"?"grid":"none";
  $("filecard").querySelectorAll(".f")[0].style.display=MODE==="restore"?"none":"grid";
  $("filecard").querySelectorAll(".f")[1].style.display=MODE==="restore"?"none":"grid";
  $("filecard").querySelectorAll(".f")[2].style.display=MODE==="restore"?"none":"grid";
  $("conncard").style.display=MODE==="restore"?"none":"block";
});

$("scan").onclick=async()=>{
  const dir=$("fdir").value.trim(); if(!dir)return;
  localStorage.setItem("pf_dir",dir);
  const r=await jpost("/api/scan",{dir}); if(r.error)return alert(r.error);
  const m=r.matches[DEV.key]||{};
  const put=(el,arr,st)=>{ if(arr[0]){$(el).value=arr[0];$(st).innerHTML='<span class=ok-tag>✔ 已匹配</span>';}
    else {$(st).innerHTML='<span class=miss>未找到</span>';} };
  put("bl2",m.bl2,"bl2s");put("fip",m.fip,"fips");put("sys",m.sys,"syss");
};
async function doFetch(online){
  const b=$("fetchfw"),old=b.textContent;
  b.disabled=true;b.textContent=online?"联网拉取中…":"释放中…";
  try{
    const d=$("fdir").value.trim();
    const r=await jpost("/api/firmware_fetch",{device:DEV.key,dir:d,online:!!online});
    if(r.error)return alert(r.error);
    const lab=(sz)=>'<span class=ok-tag>✔ '+(sz/1024).toFixed(0)+'KB</span>';
    if(r.bl2){$("bl2").value=r.bl2;$("bl2s").innerHTML=lab(r.bl2_size);}
    if(r.fip){$("fip").value=r.fip;$("fips").innerHTML=lab(r.fip_size);}
    if(!r.bl2||!r.fip)alert("未完全匹配该机型，请手动准备文件。");
    else alert((r.source==="bundled"?"已使用 exe 内置引导包（":"已从 uboot-an758x ")+r.tag+
      (r.source==="bundled"?"）":"")+"：\nBL2 + U-Boot 已就位（目录："+r.dir+"）。\n\n"+
      "系统固件 *-sysupgrade.itb 需自备：恩山论坛或自行编译。");
  }catch(e){alert("获取失败："+e)}
  finally{b.disabled=false;b.textContent=old}
}
$("fetchfw").onclick=()=>doFetch(false);
$("fetchfwOnline").onclick=()=>doFetch(true);
$("probemac").onclick=async()=>{
  const r=await jpost("/api/probe_mac",{ip:$("ip").value.trim(),port:8080});
  if(r.mac){$("mac").value=r.mac;pick();}else alert(r.error||"读取失败");
};
async function checkTool(){
  const r=await jget("/api/tool");
  $("toolst").innerHTML=r.present?'<span class=ok-tag>✔ 已就位</span>':'<span class=miss>缺失（放 files/ 目录或上传）</span>';
}
$("uploadtool").onclick=()=>$("toolfile").click();
$("toolfile").onchange=async e=>{
  const f=e.target.files[0]; if(!f)return;
  const buf=await f.arrayBuffer();
  const fd=new FormData(); fd.append("file",f);
  const r=await fetch("/api/upload_tool",{method:"POST",body:buf});
  const j=await r.json(); if(j.error)return alert(j.error);
  checkTool();
};

$("start").onclick=async()=>{
  const cfg={mode:MODE,device:DEV.key,ip:$("ip").value.trim(),
    mac:$("mac").value.trim(),telnet_user:$("tuser").value.trim(),
    telnet_pass:$("tpass").value.trim(),su_pass:$("spass").value.trim()};
  if(MODE==="full"){cfg.bl2=$("bl2").value.trim();cfg.fip=$("fip").value.trim();cfg.sys=$("sys").value.trim();}
  if(MODE==="restore"){cfg.backup_dir=$("bdir").value.trim();}
  if(!confirm(MODE==="full"?"即将开始刷机。已确认网线插千兆口、光猫供电稳定？":"确认开始？"))return;
  const r=await jpost("/api/start",cfg);
  if(r.error)return alert(r.error);
  $("panel").style.display="block";$("abort").style.display="inline-block";$("start").disabled=true;
  TIMER=setInterval(poll,1000);poll();
};
$("abort").onclick=async()=>{ if(confirm("确认中止？正在写入的操作无法安全中断。")){
  await jpost("/api/abort");}};
$("mok").onclick=()=>answer(true); $("mdeny").onclick=()=>answer(false);
async function answer(ok){
  $("modal").style.display="none";
  await jpost("/api/confirm",{ok});
}
async function poll(){
  const s=await jget("/api/state"); STATE=s;
  if(!s.idle&&s.steps){
    $("steps").innerHTML=s.steps.map(x=>
     `<li data-st="${x.state}"><span class=st>${
       {pending:"·",running:"▸",done:"✔",confirm:"!",error:"✖",}[x.state]||"·"}</span>
      <span class=nm>${esc(x.name)}</span><span class=dt>${esc(x.detail||"")}</span></li>`).join("");
    $("logs").innerHTML=(s.logs||[]).map(l=>{
      return '<div class="'+(l.startsWith("[!!]")?"err":"")+'">'+esc(l)+"</div>"}).join("");
    const lg=$("logs"); lg.scrollTop=lg.scrollHeight;
    if(s.pending_confirm){
      $("mtext").textContent=s.pending_confirm.message;
      $("modal").style.display="flex";
    } else $("modal").style.display="none";
    if(s.finished){
      clearInterval(TIMER);
      $("abort").style.display="none";$("start").disabled=false;
      if(s.error)alert("流程出错：\n"+s.error);
      else if(s.backup_dir&&!s.error)console.log("备份目录：",s.backup_dir);
    }
  }
}
init();
</script>
</body>
</html>
"""
