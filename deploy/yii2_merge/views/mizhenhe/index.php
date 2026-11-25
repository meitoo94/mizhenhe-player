<?php
/* @var $this yii\web\View */
use yii\helpers\Html;

$this->title = '米帧盒播放器 · 渠道展示与接口接入';
$siteName = (string)(Yii::$app->params['siteName'] ?? '播放器管理后台');
?>
<div style="background:#fff;border-radius:12px;padding:16px 18px;margin:0 0 12px;box-shadow:0 1px 3px rgba(0,0,0,.06)">
    <h2 style="margin:0 0 8px;font-size:19px">🎬 米帧盒播放器 · 渠道展示与接口接入</h2>
    <div style="color:#6b7280;font-size:13px;line-height:1.9">
        渠道展示、开屏倒计时 / 试看 / VIP 充值配置，以及<b>原后台接口接入状态</b>都在这个面板里。<br>
        已与 <?= Html::encode($siteName) ?> 共用同一账号、同一登录会话，<b>无需二次输入密码</b>；
        左侧「数据统计 / 用户管理 / 充值记录 / 支付配置 / 回传管理」仍是原后台。<br>
        米帧盒的注册、下单、安装归因、账号注销已桥接到原后台 <code>/api/*</code>，
        写操作只放行新建测试渠道 <code>mzhtest</code>，跑量渠道保持只读。
        &nbsp;<a href="/mzh/admin" target="_blank" rel="noopener">在新标签打开 ↗</a>
    </div>
</div>
<div style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)">
    <iframe src="/mzh/admin" title="米帧盒渠道展示后台"
            style="display:block;width:100%;height:calc(100vh - 230px);min-height:640px;border:0"></iframe>
</div>
