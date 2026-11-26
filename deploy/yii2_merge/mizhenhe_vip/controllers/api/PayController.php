<?php
namespace app\controllers\api;

use Yii;
use app\models\AlipayGateway;
use app\models\ChargePack;
use app\models\PayChannel;
use app\models\RechargeOrder;
use app\models\VipPlan;
use app\models\VipPlanChannel;
use app\models\ChannelInstall;

/**
 * 充值 / 支付接口
 *
 *   GET  /api/pay/packs    充值套餐 + 可用支付方式 + 当前余额
 *   POST /api/pay/create   创建订单，返回可直接打开的支付地址 pay_url
 *   GET  /api/pay/status   查询订单状态（App 支付后轮询）
 *   GET  /api/pay/return   支付宝同步回跳落地点（浏览器里显示「请返回 App」）
 *
 * 重要：
 *   - 订单表、加币逻辑、支付宝验签全部复用网站现有实现（RechargeOrder / AlipayGateway），
 *     异步通知仍然打到原来的 /book/pay-notify，App 不需要另起一套回调。
 *   - 微信支付尚未开通：create 传 pay_type=wxpay 会明确返回 400 + reserved=true，
 *     对接位置见方法 buildPayUrl() 里的注释。
 */
class PayController extends ApiController
{
    protected $publicActions = ['packs', 'return', 'vip-plans'];
    /** 套餐接口允许带 token（返回余额），也允许不带 */
    protected $optionalAuth = true;

    /**
     * 测试账号：登录后所有档位一律按 0.1 元下单（见 RechargeOrder::TEST_USERNAMES / TEST_AMOUNT）
     * 名单和金额只在订单模型里维护一份，这里不再重复定义
     */

    /** 当前登录用户是否测试账号 */
    protected function isTestPriceUser()
    {
        return RechargeOrder::isTestUser($this->user);
    }

    /** 展示价格：测试账号统一 0.1，其他原价 */
    protected function viewPrice($price)
    {
        return $this->isTestPriceUser() ? RechargeOrder::TEST_AMOUNT : (float)$price;
    }

    /** 价格文本：去掉多余的 0，如 0.10 → 0.1 */
    protected function viewPriceText($price)
    {
        return rtrim(rtrim(number_format((float)$price, 2, '.', ''), '0'), '.');
    }

    /**
     * 下单金额套用测试价（会员订单 / 书币订单都走这里）
     *
     * 复用了 10 分钟内的旧待支付订单时，也要把金额拉回测试价，
     * 具体逻辑见 RechargeOrder::applyTestAmount()。
     */
    protected function applyTestPrice($order)
    {
        RechargeOrder::applyTestAmount($order, $this->user);
    }

    /**
     * 当前请求的渠道号
     *
     * 登录用户优先用账号固化的渠道；未登录（还没登录就看会员页的阶段）用请求头
     * X-Device 反查启动上报时记下的渠道（channel_install）。都取不到 = 默认价。
     *
     * 注意：不接受 URL 参数，避免有人拼 ?qd=xxx 拿低价下单。
     *
     * @return string
     */
    protected function currentChannelCode()
    {
        if ($this->user !== null) {
            $code = trim((string)$this->user->channel_code);
            if ($code !== '') {
                return $code;
            }
        }
        $device = trim((string)Yii::$app->request->headers->get('X-Device', ''));
        if ($device === '') {
            return '';
        }
        try {
            return (string)ChannelInstall::codeByDevice($device);
        } catch (\Throwable $e) {
            return '';
        }
    }

    /**
     * 复用的待支付订单要按当前渠道价校正金额
     *
     * 用户先按默认价下了一单没付，换了渠道再点开通，10 分钟内会复用旧单；
     * 金额必须跟着渠道价走，否则付的还是旧价。
     */
    protected function applyChannelPrice($order, VipPlan $plan)
    {
        if ($this->isTestPriceUser() || !$order) {
            return;
        }
        if ((int)$order->status !== RechargeOrder::STATUS_PENDING) {
            return;
        }
        $price = round((float)$plan->price, 2);
        if ($price <= 0 || abs((float)$order->amount - $price) < 0.005) {
            return;
        }
        $order->amount = number_format($price, 2, '.', '');
        $origin = (float)$plan->origin_price > 0 ? (float)$plan->origin_price : $price;
        $order->origin_amount = number_format($origin, 2, '.', '');
        $order->updated_at = time();
        $order->save(false);
    }

    /**
     * 充值套餐
     */
    public function actionPacks()
    {
        $packs = ChargePack::activePacks();

        $items = [];
        $recommendId = 0;
        foreach ($packs as $p) {
            $rec = (int)$p->is_recommend === 1 ? 1 : 0;
            if ($rec && !$recommendId) {
                $recommendId = (int)$p->id;
            }
            $price = $this->viewPrice($p->price);
            $items[] = [
                'id' => (int)$p->id,
                'title' => (string)$p->title,
                'coins' => (int)$p->coins,
                'price' => $price,
                'price_text' => $this->viewPriceText($price),
                'bonus_text' => (string)$p->bonus_text,
                'badge' => (string)$p->badge,
                'is_recommend' => $rec,
            ];
        }
        // 没设推荐（或推荐的下架了）→ 前台回落到第一个
        if (!$recommendId && $items) {
            $recommendId = (int)$items[0]['id'];
        }

        // 支付方式：支付宝按后台通道是否配好动态判断；微信先占位
        $alipayReady = $this->findAlipayChannel() !== null;
        $channels = [
            [
                'key' => 'alipay',
                'name' => '支付宝',
                'available' => $alipayReady ? 1 : 0,
                'tip' => $alipayReady ? '推荐使用支付宝支付' : '支付通道未配置',
            ],
            [
                'key' => 'wxpay',
                'name' => '微信支付',
                'available' => 0,
                'reserved' => 1,
                'tip' => '即将开通，敬请期待',
            ],
        ];

        return $this->ok([
            'items' => $items,
            'channels' => $channels,
            'recommend_id' => $recommendId,
            'balance' => $this->user ? (int)$this->user->coins : 0,
            'is_guest' => $this->isGuestUser() ? 1 : 0,
            // 小说 VIP
            'vip' => $this->vipPlansPayload(VipPlan::SCOPE_NOVEL),
            'is_vip' => $this->user && $this->user->isVip() ? 1 : 0,
            'vip_expire_at' => $this->user ? $this->user->vipExpireAt() : 0,
            'vip_days_left' => $this->user ? $this->user->vipDaysLeft() : 0,
            // 浏览器 VIP（浏览首页全部功能）
            'browser_vip' => $this->vipPlansPayload(VipPlan::SCOPE_BROWSER),
            'is_browser_vip' => $this->user && $this->user->isBrowserVip() ? 1 : 0,
            'browser_vip_expire_at' => $this->user ? $this->user->browserVipExpireAt() : 0,
            'browser_vip_days_left' => $this->user ? $this->user->browserVipDaysLeft() : 0,
        ]);
    }

    /**
     * 会员档位（书币充值页 / 会员购买弹层共用）
     *
     * @param string $scope novel 小说VIP / browser 浏览器VIP / mizhenhe 米帧盒VIP
     * @return array
     */
    protected function vipPlansPayload($scope = VipPlan::SCOPE_NOVEL)
    {
        $out = [];
        $test = $this->isTestPriceUser();
        $chCode = $this->currentChannelCode();
        foreach (VipPlan::activePlans($scope) as $p) {
            // 渠道专属价：命中就用渠道价，没配过就是默认价（只改内存对象，不落库）
            $eff = VipPlanChannel::effective($p, $chCode);
            if ((float)$eff['price'] > 0) {
                $p->price = $eff['price'];
                $p->origin_price = $eff['origin_price'];
                // 角标也跟着渠道走（留空沿用默认档，减号 = 不显示）
                $p->badge = (string)$eff['badge'];
            }
            $price = $this->viewPrice($p->price);
            $out[] = [
                'id' => (int)$p->id,
                'code' => (string)$p->code,
                'scope' => $p->scopeKey(),
                'title' => (string)$p->title,
                'days' => (int)$p->days,
                'price' => $price,
                'price_text' => $this->viewPriceText($price),
                // 测试账号价格与划线价一致，避免前台出现「0.1 元原价 30 元」的奇怪促销样式
                'origin_price' => $test ? $price : (float)$p->origin_price,
                'origin_price_text' => $test ? $this->viewPriceText($price) : $p->originPriceText(),
                'per_day_text' => $test ? '' : $p->perDayText(),
                'saving_text' => $test ? '' : $p->savingText(),
                'badge' => (string)$p->badge,
            ];
        }
        return $out;
    }

    /**
     * 会员档位单独取（VIP 封面 / 我的页会员卡片 / 浏览器 VIP 页）
     *
     * GET /api/pay/vip-plans?scope=novel|browser|mizhenhe
     */
    public function actionVipPlans()
    {
        $scope = VipPlan::normalizeScope($this->input('scope', VipPlan::SCOPE_NOVEL));
        $vipName = $scope === VipPlan::SCOPE_BROWSER ? '浏览器VIP'
            : ($scope === VipPlan::SCOPE_MIZHENHE ? '米帧盒VIP' : '小说VIP');

        return $this->ok([
            'scope' => $scope,
            'items' => $this->vipPlansPayload($scope),
            // 两套会员互不影响：is_vip / 到期时间都按当前 scope 返回，
            // 前端只需认 scope 对应这一组字段即可
            'is_vip' => $this->user && $this->user->isVipScope($scope) ? 1 : 0,
            'vip_expire_at' => $this->user ? $this->user->vipExpireAtScope($scope) : 0,
            'vip_days_left' => $this->user ? $this->user->vipDaysLeftScope($scope) : 0,
            'is_browser_vip' => $this->user && $this->user->isBrowserVip() ? 1 : 0,
            'browser_vip_expire_at' => $this->user ? $this->user->browserVipExpireAt() : 0,
            'is_novel_vip' => $this->user && $this->user->isVip() ? 1 : 0,
            'novel_vip_expire_at' => $this->user ? $this->user->vipExpireAt() : 0,
            'vip_name' => $vipName,
        ]);
    }

    /**
     * 创建会员订单
     *
     * 参数：plan_id / pay_type(alipay) / scope(novel 默认 | browser | mizhenhe)
     */
    public function actionVipCreate()
    {
        $this->requirePost();
        $user = $this->requireUser();

        if ($this->isGuestUser()) {
            return $this->fail('游客账号不能开通会员，请先注册正式账号', 403, ['need_register' => 1]);
        }

        $scope = VipPlan::normalizeScope($this->input('scope', VipPlan::SCOPE_NOVEL));
        $planId = (int)$this->input('plan_id', $this->input('id', 0));

        $plan = null;
        if ($planId > 0) {
            $plan = VipPlan::find()
                ->where(['id' => $planId, 'is_active' => 1, 'scope' => $scope])
                ->one();
            // 老客户端下单不带 scope（默认会被当成小说），浏览器档位就会查不到。
            // 这里按 id 忽略 scope 再找一次，避免老包永远开不了会员。
            if (!$plan) {
                $plan = VipPlan::find()
                    ->where(['id' => $planId, 'is_active' => 1])
                    ->one();
            }
        }
        // 兜底：有的版本只传 code
        if (!$plan) {
            $planCode = trim((string)$this->input('plan_code', $this->input('code', '')));
            if ($planCode !== '') {
                $plan = VipPlan::find()
                    ->where(['code' => $planCode, 'is_active' => 1, 'scope' => $scope])
                    ->one();
                if (!$plan) {
                    $plan = VipPlan::find()
                        ->where(['code' => $planCode, 'is_active' => 1])
                        ->one();
                }
            }
        }
        if (!$plan) {
            // 留痕：线上再出现「档位不存在」时直接看这个文件，不用再猜前端传了什么
            @file_put_contents(
                Yii::getAlias('@runtime/logs/vip_create.log'),
                date('Y-m-d H:i:s')
                . ' fail plan_id=' . $planId
                . ' scope=' . $scope
                . ' code=' . (string)$this->input('plan_code', $this->input('code', ''))
                . ' ua=' . substr((string)Yii::$app->request->userAgent, 0, 80)
                . "\n",
                FILE_APPEND
            );
            return $this->fail('会员档位不存在或已下架', 400);
        }
        // 以档位自身的 scope 为准（客户端可能没传或传错），订单 vip_scope / 加权益都跟着对
        $scope = $plan->scopeKey();

        // 渠道专属价：金额一律服务端按渠道重算，不采信客户端传来的价格
        $eff = VipPlanChannel::effective($plan, $this->currentChannelCode());
        if ((float)$eff['price'] > 0) {
            $plan->price = $eff['price'];
            $plan->origin_price = $eff['origin_price'];
        }

        $payType = strtolower(trim((string)$this->input('pay_type', 'alipay')));
        if ($payType === 'wxpay') {
            return $this->fail('微信支付正在接入中，请先使用支付宝', 400, ['reserved' => 1, 'pay_type' => 'wxpay']);
        }
        if ($payType !== 'alipay') {
            return $this->fail('不支持的支付方式', 400);
        }

        $channel = $this->findAlipayChannel();
        if (!$channel) {
            return $this->fail('支付宝支付通道未配置，请联系客服', 400);
        }

        // 同一档位 10 分钟内的待支付订单直接复用，避免反复点「立即开通」重复下单
        $query = RechargeOrder::find()
            ->where([
                'user_id' => (int)$user->id,
                'vip_plan_id' => (int)$plan->id,
                'status' => RechargeOrder::STATUS_PENDING,
            ])
            ->andWhere(['>=', 'created_at', time() - 600]);
        if ((new RechargeOrder())->hasAttribute('vip_scope')) {
            $query->andWhere(['vip_scope' => $scope]);
        }
        $order = $query->orderBy(['id' => SORT_DESC])->one();

        if (!$order) {
            $order = RechargeOrder::createPendingVip($user, $plan, [
                'pay_type' => 'alipay',
                'channel' => $channel->merchant_alias ?: $channel->pay_mark,
                'ip' => (string)Yii::$app->request->userIP,
                'remark' => $scope === VipPlan::SCOPE_BROWSER ? 'app-browser-vip'
                    : ($scope === VipPlan::SCOPE_MIZHENHE ? 'app-mizhenhe-vip' : 'app-vip'),
            ]);
        }

        // 复用了 10 分钟内的旧待支付订单：金额拉回当前渠道价
        $this->applyChannelPrice($order, $plan);

        // 测试账号：金额固定 0.1 元（权益仍按原档位发放）
        $this->applyTestPrice($order);

        $payUrl = $this->buildPayUrl($order, null, $channel);
        // 应用外拉起：不再生成 APP 支付签名串（APP 支付未签约，拉起必报参数异常）
        $orderStr = '';
        if ($payUrl === '') {
            return $this->fail('支付参数生成失败，请检查支付宝配置（应用私钥 / 支付宝公钥）', 500);
        }

        return $this->ok([
            'order_no' => (string)$order->order_no,
            'plan_id' => (int)$plan->id,
            'scope' => $scope,
            'title' => $plan->productPrefix() . '·' . $plan->title,
            'amount' => (float)$order->amount,
            'days' => (int)$plan->days,
            'pay_type' => 'alipay',
            // 应用外拉起：客户端直接用 pay_url 跳系统浏览器（手机网站支付）
            'pay_mode' => 'wap',
            'pay_url' => $payUrl,
            'status' => (int)$order->status,
            'expires_at' => (int)$order->created_at + RechargeOrder::PENDING_TTL,
        ], '订单创建成功');
    }

    /**
     * 创建充值订单
     *
     * 参数：pack_id / pay_type(alipay|wxpay)
     */
    public function actionCreate()
    {
        $this->requirePost();
        $user = $this->requireUser();

        if ($this->isGuestUser()) {
            return $this->fail('游客账号不能充值，请先注册正式账号', 403, ['need_register' => 1]);
        }

        $packId = (int)$this->input('pack_id', $this->input('id', 0));
        $pack = ChargePack::find()->where(['id' => $packId, 'is_active' => 1])->one();
        if (!$pack) {
            return $this->fail('套餐不存在或已下架', 400);
        }

        $payType = strtolower(trim((string)$this->input('pay_type', 'alipay')));

        // 微信支付：预留位置（对接时在这里补下单逻辑，见 buildPayUrl 注释）
        if ($payType === 'wxpay') {
            return $this->fail('微信支付正在接入中，请先使用支付宝', 400, ['reserved' => 1, 'pay_type' => 'wxpay']);
        }
        if ($payType !== 'alipay') {
            return $this->fail('不支持的支付方式', 400);
        }

        $channel = $this->findAlipayChannel();
        if (!$channel) {
            return $this->fail('支付宝支付通道未配置，请联系客服', 400);
        }

        // 同一套餐 10 分钟内的待支付订单直接复用，避免重复下单（反复点「立即充值」）
        $order = RechargeOrder::find()
            ->where(['user_id' => (int)$user->id, 'pack_id' => (int)$pack->id, 'status' => RechargeOrder::STATUS_PENDING])
            ->andWhere(['>=', 'created_at', time() - 600])
            ->orderBy(['id' => SORT_DESC])->one();

        if (!$order) {
            $order = RechargeOrder::createPending($user, $pack, [
                'pay_type' => 'alipay',
                'channel' => $channel->merchant_alias ?: $channel->pay_mark,
                'ip' => (string)Yii::$app->request->userIP,
                'remark' => 'app',
            ]);
        }

        // 测试账号：金额固定 0.1 元（书币数量仍按原套餐发放）
        $this->applyTestPrice($order);

        $payUrl = $this->buildPayUrl($order, $pack, $channel);
        // 应用外拉起：不再生成 APP 支付签名串（APP 支付未签约，拉起必报参数异常）
        $orderStr = '';
        if ($payUrl === '') {
            return $this->fail('支付参数生成失败，请检查支付宝配置（应用私钥 / 支付宝公钥）', 500);
        }

        return $this->ok([
            'order_no' => (string)$order->order_no,
            'pack_id' => (int)$pack->id,
            'title' => (string)$pack->title,
            'amount' => (float)$order->amount,
            'coins' => (int)$order->coins,
            'pay_type' => 'alipay',
            // 应用外拉起：客户端直接用 pay_url 跳系统浏览器（手机网站支付）
            'pay_mode' => 'wap',
            'pay_url' => $payUrl,
            'status' => (int)$order->status,
            'expires_at' => (int)$order->created_at + RechargeOrder::PENDING_TTL,
        ], '订单创建成功');
    }

    /**
     * 查询订单状态（支付后轮询，同时返回最新余额）
     */
    public function actionStatus()
    {
        $user = $this->requireUser();
        $orderNo = trim((string)$this->input('order_no', ''));
        if ($orderNo === '') {
            return $this->fail('缺少订单号', 400);
        }

        $order = RechargeOrder::find()
            ->where(['order_no' => $orderNo, 'user_id' => (int)$user->id])->one();
        if (!$order) {
            return $this->fail('订单不存在', 404);
        }

        // 超时未支付自动关单（与网站收银台一致）
        if ((int)$order->status === RechargeOrder::STATUS_PENDING
            && (int)$order->created_at + RechargeOrder::PENDING_TTL < time()) {
            $order->close();
            $order->status = RechargeOrder::STATUS_CLOSED;
        }

        $fresh = \app\models\User::findOne((int)$user->id);

        return $this->ok([
            'order_no' => (string)$order->order_no,
            'status' => (int)$order->status,
            'status_text' => $order->getStatusText(),
            'paid' => (int)$order->status === RechargeOrder::STATUS_PAID ? 1 : 0,
            'amount' => (float)$order->amount,
            'coins' => (int)$order->coins,
            'pay_type' => (string)$order->pay_type,
            'is_vip_order' => $order->isVipOrder() ? 1 : 0,
            'vip_days' => $order->hasAttribute('vip_days') ? (int)$order->vip_days : 0,
            'vip_scope' => $order->isVipOrder() ? $order->vipScope() : '',
            'created_at' => (int)$order->created_at,
            'paid_at' => (int)$order->paid_at,
            'expires_at' => (int)$order->created_at + RechargeOrder::PENDING_TTL,
            'balance' => $fresh ? (int)$fresh->coins : 0,
            'is_vip' => $fresh && $fresh->isVip() ? 1 : 0,
            'vip_expire_at' => $fresh ? $fresh->vipExpireAt() : 0,
            'vip_days_left' => $fresh ? $fresh->vipDaysLeft() : 0,
            'is_browser_vip' => $fresh && $fresh->isBrowserVip() ? 1 : 0,
            'browser_vip_expire_at' => $fresh ? $fresh->browserVipExpireAt() : 0,
            'browser_vip_days_left' => $fresh ? $fresh->browserVipDaysLeft() : 0,
        ]);
    }

    /**
     * 支付宝同步回跳落地页
     *
     * 支付宝付完款会在浏览器里跳到这里：
     *   1. 若回调参数带签名，先按网站同样的规则验签并结算（同步兜底，异步通知仍会到 /book/pay-notify）
     *   2. 输出一个极简 H5，提示「支付完成，请返回 App」
     */
    public function actionReturn()
    {
        $params = array_merge(Yii::$app->request->get(), Yii::$app->request->post());
        $this->settleIfVerified($params);

        $orderNo = isset($params['out_trade_no']) ? (string)$params['out_trade_no'] : '';
        $paid = false;
        $isMizhenhe = false;
        if ($orderNo !== '') {
            $order = RechargeOrder::find()->where(['order_no' => $orderNo])->one();
            $paid = $order && in_array((int)$order->status, [RechargeOrder::STATUS_PAID, RechargeOrder::STATUS_REFUNDED], true);
            // 米帧盒 VIP 订单走专属回跳页；小说 / 浏览器两套页面完全不动
            if ($order && $order->isVipOrder() && $order->vipScope() === VipPlan::SCOPE_MIZHENHE) {
                $isMizhenhe = true;
            }
        }

        Yii::$app->response->format = \yii\web\Response::FORMAT_HTML;
        return $this->renderFile($isMizhenhe
            ? '@app/views/api/pay-return-mizhenhe.php'
            : '@app/views/api/pay-return.php', [
            'paid' => $paid,
            'orderNo' => $orderNo,
        ]);
    }

    // ------------------------------------------------------------------
    // 内部方法
    // ------------------------------------------------------------------

    /**
     * 取可用的支付宝官方通道
     * @return PayChannel|null
     */
    protected function findAlipayChannel()
    {
        foreach (PayChannel::activeChannels('alipay') as $c) {
            if (AlipayGateway::isConfigured($c)) {
                return $c;
            }
        }
        return null;
    }

    /**
     * 生成支付跳转地址
     *
     * 【微信支付对接位置】
     *   微信 App 支付（推荐，uni-app 原生 + 微信 SDK）：
     *     1. 在 PayChannel 里新增 pay_type=wxpay 的通道（appid / mchid / 商户私钥 / 证书序列号）
     *     2. 这里调用微信统一下单 v3：POST https://api.mch.weixin.qq.com/v3/pay/transactions/app
     *        body = {appid, mchid, description, out_trade_no, notify_url, amount:{total: 分}}
     *        返回 prepay_id → 用商户私钥签名拼出调起参数（appid/partnerid/prepayid/package/noncestr/timestamp/sign）
     *     3. 接口返回 pay_type=wxpay + pay_params（给 App 端 uni.requestPayment 用）
     *   微信 H5 支付（在 App 内 webview 打开）：
     *     走 /v3/pay/transactions/h5，返回 h5_url，直接把 pay_url 填成该地址即可。
     *   ⚠️ 微信回调必须另开一个 /api/pay/wx-notify，用微信平台证书验签后再调用
     *      RechargeOrder::settle($order, 'wxpay', $transactionId) 加币。
     *
     * @param RechargeOrder $order
     * @param ChargePack|null $pack 会员订单传 null（不参与签名）
     * @param PayChannel $channel
     * @return string 支付地址（空串表示失败）
     */
    protected function buildPayUrl($order, $pack, $channel)
    {
        $base = $this->baseUrl();
        // 异步通知仍走网站原有的 /book/pay-notify（已验证可用，不用另写一套）
        $notifyUrl = $base . Yii::$app->urlManager->createUrl(['book/pay-notify']);
        $returnUrl = $base . Yii::$app->urlManager->createUrl(['api/pay/return']);

        $params = AlipayGateway::wapPayParams(
            $channel,
            $order->order_no,
            $order->amount,
            $order->getProductTitleText(),
            $notifyUrl,
            $returnUrl,
            ['quit_url' => $returnUrl]
        );
        if (!$params) {
            Yii::error('[api] alipay 参数生成失败 channel#' . $channel->id, 'pay');
            return '';
        }

        $gateway = AlipayGateway::gatewayUrl($channel);
        // 网关地址里已带 ?charset=utf-8，而 $params 里也有 charset，
        // 拼成 GET 时会出现两个 charset，支付宝可能因此验签失败 —— 先去掉网关上的
        $gateway = preg_replace('/([?&])charset=[^&]*&?/', '$1', $gateway);
        $gateway = rtrim($gateway, '?&');

        return $gateway . (strpos($gateway, '?') === false ? '?' : '&') . http_build_query($params);
    }

    /**
     * App 支付签名串（alipay.trade.app.pay）
     *
     * 客户端优先用这个在**应用内**拉起支付宝（uni.requestPayment），
     * 生成失败才退回 pay_url（手机网站支付，会跳到系统浏览器）。
     *
     * @return string 空 = 通道没配 App 支付 / 签名失败
     */
    protected function buildAppOrderStr($order, $pack, $channel)
    {
        $base = $this->baseUrl();
        $notifyUrl = $base . Yii::$app->urlManager->createUrl(['book/pay-notify']);

        $str = AlipayGateway::appPayOrderStr(
            $channel,
            $order->order_no,
            $order->amount,
            $order->getProductTitleText(),
            $notifyUrl
        );
        if ($str === '') {
            Yii::error('[api] alipay App 支付签名失败 channel#' . $channel->id, 'pay');
        }
        return $str;
    }

    /**
     * 同步回跳验签结算（失败静默，异步通知会兜底）
     *
     * @param array $params
     * @return bool
     */
    protected function settleIfVerified(array $params)
    {
        if (!AlipayGateway::looksLikeAlipay($params) || empty($params['out_trade_no'])) {
            return false;
        }
        $order = RechargeOrder::find()->where(['order_no' => (string)$params['out_trade_no']])->one();
        if (!$order || (int)$order->status !== RechargeOrder::STATUS_PENDING) {
            return false;
        }

        $channels = array_merge(PayChannel::activeChannels('alipay'), PayChannel::find()->where(['pay_type' => 'alipay'])->all());
        $seen = [];
        foreach ($channels as $c) {
            if (isset($seen[$c->id])) {
                continue;
            }
            $seen[$c->id] = 1;
            list($ok) = AlipayGateway::verifyNotify($params, $c);
            if (!$ok) {
                continue;
            }
            $status = strtoupper((string)($params['trade_status'] ?? ''));
            if ($status !== '' && !in_array($status, ['TRADE_SUCCESS', 'TRADE_FINISHED'], true)) {
                return false;
            }
            if (isset($params['total_amount']) && abs((float)$params['total_amount'] - (float)$order->amount) >= 0.01) {
                return false;
            }
            list($settled) = RechargeOrder::settle($order, 'alipay', (string)($params['trade_no'] ?? ''));
            return (bool)$settled;
        }
        return false;
    }
}
