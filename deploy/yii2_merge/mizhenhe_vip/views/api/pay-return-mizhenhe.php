<?php
/**
 * 米帧盒播放器 · 支付同步回跳页（scope=mizhenhe 专用）
 *
 * 书城 / 浏览器 VIP 仍走原来的 pay-return.php，这里只服务米帧盒订单，
 * 视觉沿用 App 的深色主题，文案只讲会员权益，不出现「书币 / 继续阅读」。
 *
 * @var bool   $paid
 * @var string $orderNo
 */
$title = $paid ? '会员已开通' : '支付结果确认中';
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0B1020">
<title><?= htmlspecialchars($title, ENT_QUOTES, 'UTF-8') ?> · 米帧盒播放器</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif;
 background:radial-gradient(120% 80% at 50% 0%,#221340 0%,#0B1020 62%,#070A14 100%);
 color:#F2F6FF;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.wrap{width:100%;max-width:380px}
.brand{font-size:11px;letter-spacing:.28em;color:#A78BFA;margin-bottom:20px;text-align:center}
.card{background:#151C30;border:1px solid rgba(167,139,250,.22);border-radius:24px;
 padding:34px 24px 26px;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.45)}
.ico{width:72px;height:72px;margin:0 auto 20px;border-radius:22px;display:flex;
 align-items:center;justify-content:center;
 background:linear-gradient(135deg,#5B21B6,#140A2E);border:1px solid rgba(196,181,253,.35)}
.ico svg{width:34px;height:34px}
h1{font-size:22px;font-weight:700;letter-spacing:.02em;margin-bottom:8px}
.sub{font-size:13px;color:#A78BFA;margin-bottom:18px}
.desc{font-size:14px;color:#C7CEE0;line-height:1.85;text-align:left}
.desc b{color:#F2F6FF;font-weight:600}
.order{margin-top:18px;padding:11px 12px;border-radius:12px;background:#0F1526;
 font-size:11px;color:#8A96B4;word-break:break-all;text-align:left}
.perks{margin-top:18px;text-align:left;display:flex;flex-direction:column;gap:9px}
.perk{display:flex;gap:9px;align-items:flex-start;font-size:12px;color:#9AA6C2;line-height:1.6}
.dot{width:5px;height:5px;border-radius:50%;background:#A78BFA;flex:none;margin-top:7px}
.btn{display:block;margin-top:26px;padding:15px 0;border-radius:999px;
 background:linear-gradient(135deg,#7C3AED,#4C1D95);color:#fff;font-size:16px;
 font-weight:700;text-decoration:none;border:0;width:100%;
 box-shadow:0 10px 26px rgba(91,33,182,.45)}
.hint{margin-top:14px;font-size:11px;color:#6F7A96;line-height:1.7}
.foot{margin-top:22px;text-align:center;font-size:11px;color:#5C6684;letter-spacing:.14em}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">MIZHENHE PLAYER</div>
  <div class="card">
    <div class="ico">
      <?php if ($paid): ?>
      <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>
      <?php else: ?>
      <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
      <?php endif; ?>
    </div>
    <h1><?= htmlspecialchars($title, ENT_QUOTES, 'UTF-8') ?></h1>
    <div class="sub">米帧盒播放器 · 会员权益</div>
    <p class="desc">
      <?= $paid
        ? '支付已完成，<b>米帧盒会员权益已开通</b>。请返回「米帧盒播放器」App 继续使用。'
        : '我们正在向支付平台确认这笔订单。如已完成付款，会员权益通常会在几秒内自动开通，请返回「米帧盒播放器」App 查看。' ?>
    </p>
    <?php if ($orderNo !== ''): ?>
      <div class="order">订单号：<?= htmlspecialchars($orderNo, ENT_QUOTES, 'UTF-8') ?></div>
    <?php endif; ?>
    <div class="perks">
      <div class="perk"><span class="dot"></span><span>本地影像播放、归档、调色、拼帧全部解锁</span></div>
      <div class="perk"><span class="dot"></span><span>导入与播放全程在本机完成，不上传你的视频</span></div>
      <div class="perk"><span class="dot"></span><span>若返回后仍显示未开通，请在 App 内下拉刷新或彻底重启一次</span></div>
    </div>
    <button class="btn" onclick="window.close();">返回 App</button>
    <div class="hint">按钮没反应时，请用多任务切换手动回到「米帧盒播放器」</div>
  </div>
  <div class="foot">米帧盒播放器 · 本地影像工具</div>
</div>
</body>
</html>
