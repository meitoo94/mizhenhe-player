<?php
namespace app\models;

use Yii;
use yii\db\ActiveRecord;
use yii\db\Expression;
use yii\db\Query;

/**
 * 充值订单（后台充值统计 / 充值记录的数据源）
 *
 * @property int $id
 * @property string $order_no        平台订单号
 * @property string $channel_no      支付通道流水号
 * @property int $user_id
 * @property int $pack_id
 * @property string $product_title   商品说明
 * @property string $product_type
 * @property string|float $amount    实付金额（元）
 * @property string|float $origin_amount
 * @property int $coins              到账书币
 * @property int $bonus_coins        赠送书币
 * @property string $pay_type        wxpay/alipay/h5...
 * @property string $channel         渠道标识
 * @property int $status             0待支付 1已支付 2已退款 3已关闭
 * @property int $refund_status      0无 1退款中 2已退款
 * @property int $is_new_user        是否首充
 * @property string $deliver_no      交付单号
 * @property string $ip
 * @property string $region
 * @property string $remark
 * @property int $paid_at
 * @property int $refund_at
 * @property int $created_at
 * @property int $updated_at
 */
class RechargeOrder extends ActiveRecord
{
    const STATUS_PENDING  = 0;
    const STATUS_PAID     = 1;
    const STATUS_REFUNDED = 2;
    const STATUS_CLOSED   = 3;

    /** 视为「已付款」的状态（含退款前已付款的订单） */
    const PAID_SQL = 'status IN (1,2)';

    /**
     * 自测账号兜底名单
     *
     * 正式名单在 pay_whitelist 表里，由后台「支付相关管理 → 支付白名单」维护；
     * 这里只在表还不存在（老库没跑迁移）时兜底，保证下单主流程不受影响。
     */
    const TEST_USERNAMES = ['18968252345'];
    /** 自测账号的默认下单金额（元） */
    const TEST_AMOUNT = 0.1;

    /**
     * 该账号的专属下单金额；不在白名单返回 null
     *
     * App 与网站所有下单入口只认这一个判断，后台改了名单立刻生效。
     */
    public static function testAmountFor($user)
    {
        if ($user === null) {
            return null;
        }
        $username = (string)$user->username;
        $map = PayWhitelist::activeMap();
        if ($map === null) {
            // 表不可用：退回内置名单
            return in_array($username, self::TEST_USERNAMES, true) ? self::TEST_AMOUNT : null;
        }
        // 以用户 ID 为准：后台按 ID 添加，用户改了账号名也不会失效
        $uid = (int)(isset($user->id) ? $user->id : 0);
        if ($uid > 0 && isset($map[$uid])) {
            return (float)$map[$uid];
        }
        // 老记录（没有 user_id）按账号兜底
        $names = PayWhitelist::activeNameMap();
        return isset($names[$username]) ? (float)$names[$username] : null;
    }

    /** 该账号是否为自测账号 */
    public static function isTestUser($user)
    {
        return self::testAmountFor($user) !== null;
    }

    /**
     * 把已存在订单的金额改成测试价（下单复用了旧待支付订单时用）
     *
     * 只改金额，套餐权益（VIP 天数 / 书币）保持原样，支付链路与线上完全一致。
     */
    public static function applyTestAmount($order, $user)
    {
        $testAmount = self::testAmountFor($user);
        if (!$order || $testAmount === null) {
            return;
        }
        if (abs((float)$order->amount - $testAmount) < 0.001) {
            return;
        }
        // 原价留档，后台仍能看出真实档位价值
        if ((float)$order->origin_amount <= 0) {
            $order->origin_amount = (float)$order->amount;
        }
        $order->amount = $testAmount;
        $order->updated_at = time();
        $order->save(false);
    }

    /**
     * 统计时排除自测账号的 where 条件
     *
     * @param string $column user_id 字段，联表时传 'o.user_id' 之类的别名
     * @return array
     */
    public static function excludeTestUsersWhere($column = 'user_id')
    {
        $map = PayWhitelist::activeMap();

        if ($map === null) {
            $ids = [];
            $names = self::TEST_USERNAMES;
        } else {
            $ids = array_keys($map);
            $names = array_keys(PayWhitelist::activeNameMap());
        }

        $cond = ['or'];
        if ($ids) {
            $cond[] = ['in', 'id', $ids];
        }
        if ($names) {
            $cond[] = ['in', 'username', $names];
        }
        if (count($cond) === 1) {
            return [];
        }

        return ['NOT IN', $column, (new Query())->select('id')
            ->from(User::tableName())->where($cond)];
    }

    public static function tableName()
    {
        return '{{%recharge_order}}';
    }

    public function rules()
    {
        return [
            [['user_id', 'created_at', 'updated_at'], 'required'],
            [['user_id', 'pack_id', 'coins', 'bonus_coins', 'status', 'refund_status', 'is_new_user',
                'paid_at', 'refund_at'], 'integer'],
            [['amount', 'origin_amount'], 'number'],
            [['order_no', 'channel_no', 'deliver_no'], 'string', 'max' => 64],
            [['product_title', 'region', 'remark'], 'string', 'max' => 255],
            [['pay_type', 'channel', 'product_type'], 'string', 'max' => 50],
            [['ip'], 'string', 'max' => 45],
        ];
    }

    // ---------------- 关系 ----------------

    public function getUser()
    {
        return $this->hasOne(User::class, ['id' => 'user_id']);
    }

    public function getPack()
    {
        return $this->hasOne(ChargePack::class, ['id' => 'pack_id']);
    }

    // ---------------- 文案 ----------------

    public static function statusList()
    {
        return [
            self::STATUS_PENDING  => '待支付',
            self::STATUS_PAID     => '已支付',
            self::STATUS_REFUNDED => '已退款',
            self::STATUS_CLOSED   => '已关闭',
        ];
    }

    public static function payTypeList()
    {
        return ['wxpay' => '微信支付', 'alipay' => '支付宝', 'h5' => 'H5 支付', 'qqpay' => 'QQ 钱包'];
    }

    public function getStatusText()
    {
        $map = self::statusList();
        return isset($map[$this->status]) ? $map[$this->status] : '未知';
    }

    /** 支付状态样式：paid / pending / refunded / closed */
    public function getStatusBadge()
    {
        $map = [
            self::STATUS_PENDING => 'pending',
            self::STATUS_PAID => 'paid',
            self::STATUS_REFUNDED => 'refunded',
            self::STATUS_CLOSED => 'closed',
        ];
        return isset($map[$this->status]) ? $map[$this->status] : 'closed';
    }

    public function getPayTypeText()
    {
        $map = self::payTypeList();
        return isset($map[$this->pay_type]) ? $map[$this->pay_type] : ($this->pay_type ?: '—');
    }

    /** 套餐 id => 当前套餐名缓存（单次请求内复用，避免列表 N+1） */
    private static $_packTitleCache = null;

    /**
     * 套餐 id => 当前套餐名（套餐改名后，历史订单展示的名字自动跟着变）
     * @param bool $refresh 传 true 强制重新查库
     */
    public static function packTitleMap($refresh = false)
    {
        if ($refresh || self::$_packTitleCache === null) {
            self::$_packTitleCache = ChargePack::find()->select(['title', 'id'])->indexBy('id')->column();
        }
        return self::$_packTitleCache;
    }

    /**
     * 商品名称：优先取套餐当前名称，套餐已删除时回退到下单时的快照
     */
    public function getProductTitleText()
    {
        $pid = (int)$this->pack_id;
        if ($pid > 0) {
            $map = self::packTitleMap();
            if (isset($map[$pid]) && $map[$pid] !== '') {
                return $map[$pid];
            }
        }
        return $this->product_title !== '' && $this->product_title !== null ? $this->product_title : '—';
    }

    /**
     * 套餐改名后同步历史数据
     * - recharge_order.product_title 快照改成新名字（导出、套餐删除后仍可读）
     * - ledger 里「充值「旧套餐名」」备注一并替换
     *
     * @param int $packId
     * @param string $newTitle 新套餐名
     * @param string|null $oldTitle 旧套餐名（不传则自动从订单快照里找）
     * @return int 同步的订单条数
     */
    public static function syncPackTitle($packId, $newTitle, $oldTitle = null)
    {
        $packId = (int)$packId;
        $newTitle = (string)$newTitle;
        if ($packId <= 0 || $newTitle === '') {
            return 0;
        }

        // 该套餐历史上用过的所有旧名字
        $oldTitles = (new Query())->select('product_title')->distinct()
            ->from(static::tableName())->where(['pack_id' => $packId])
            ->andWhere(['<>', 'product_title', $newTitle])->column();
        $oldTitles = array_values(array_filter(array_map('strval', $oldTitles), function ($t) {
            return $t !== '';
        }));
        if ($oldTitle !== null && $oldTitle !== '' && $oldTitle !== $newTitle
            && !in_array($oldTitle, $oldTitles, true)) {
            $oldTitles[] = $oldTitle;
        }

        $affected = static::updateAll(['product_title' => $newTitle], ['pack_id' => $packId]);

        // 账本备注：充值「旧名」→ 充值「新名」
        foreach ($oldTitles as $ot) {
            $from = '「' . $ot . '」';
            $to = '「' . $newTitle . '」';
            foreach (Ledger::find()->where(['like', 'note', $from])->all() as $row) {
                $note = str_replace($from, $to, (string)$row->note);
                if ($note !== $row->note) {
                    $row->note = $note;
                    $row->save(false, ['note']);
                }
            }
        }

        self::$_packTitleCache = null; // 名称已变，缓存失效
        return (int)$affected;
    }

    public function getRefundText()
    {
        $map = [0 => '无退款', 1 => '退款中', 2 => '已退款'];
        return isset($map[$this->refund_status]) ? $map[$this->refund_status] : '—';
    }

    public function getAmountText()
    {
        return number_format((float)$this->amount, 2);
    }

    // ---------------- 写入 ----------------

    public static function genOrderNo()
    {
        return 'R' . date('YmdHis') . str_pad((string)mt_rand(0, 9999), 4, '0', STR_PAD_LEFT);
    }

    public static function genChannelNo()
    {
        return '4200' . date('YmdHis') . str_pad((string)mt_rand(0, 999999), 6, '0', STR_PAD_LEFT);
    }

    /**
     * 该用户此前是否没有成功充值记录（用于判定首充）
     */
    public static function isFirstPay($userId)
    {
        return !static::find()->where(['user_id' => (int)$userId])->andWhere(self::PAID_SQL)->exists();
    }

    /**
     * 前台充值成功后落一笔已支付订单（不负责加书币，由调用方在事务内处理）
     *
     * @param User $user
     * @param ChargePack $pack
     * @param array $opts pay_type / channel / ip / region / remark
     * @return static
     */
    public static function recordPaid(User $user, ChargePack $pack, array $opts = [])
    {
        $now = time();
        $o = new static();
        $o->order_no = self::genOrderNo();
        $o->channel_no = isset($opts['channel_no']) ? $opts['channel_no'] : self::genChannelNo();
        $o->user_id = (int)$user->id;
        $o->pack_id = (int)$pack->id;
        $o->product_title = $pack->title;
        $o->product_type = 'coins';
        $o->amount = $pack->price;
        $o->origin_amount = $pack->price;
        $o->coins = (int)$pack->coins;
        $o->bonus_coins = 0;
        $o->pay_type = isset($opts['pay_type']) && $opts['pay_type'] !== '' ? $opts['pay_type'] : 'wxpay';
        $o->channel = isset($opts['channel']) ? $opts['channel'] : '';
        $o->status = self::STATUS_PAID;
        $o->refund_status = 0;
        $o->is_new_user = self::isFirstPay($user->id) ? 1 : 0;
        $o->deliver_no = 'D' . date('YmdHis') . str_pad((string)mt_rand(0, 999), 3, '0', STR_PAD_LEFT);
        $o->ip = isset($opts['ip']) ? (string)$opts['ip'] : '';
        $o->region = isset($opts['region']) ? (string)$opts['region'] : '';
        $o->remark = isset($opts['remark']) ? (string)$opts['remark'] : '';
        $o->paid_at = $now;
        $o->refund_at = 0;
        $o->created_at = $now;
        $o->updated_at = $now;
        $o->save(false);
        return $o;
    }

    /**
     * 前台下单：只建「待支付」订单，不碰余额。
     * 真正加币发生在收银台确认支付时（markPaid + gainCoins）。
     *
     * @param User $user
     * @param ChargePack $pack
     * @param array $opts pay_type / channel / ip / region / remark
     * @return static
     */
    public static function createPending(User $user, ChargePack $pack, array $opts = [])
    {
        $now = time();
        $o = new static();
        $o->order_no = self::genOrderNo();
        $o->channel_no = '';
        $o->user_id = (int)$user->id;
        $o->pack_id = (int)$pack->id;
        $o->product_title = $pack->title;
        $o->product_type = 'coins';
        $testAmount = self::testAmountFor($user);
        $o->amount = $testAmount !== null ? $testAmount : $pack->price;
        $o->origin_amount = $pack->price;
        $o->coins = (int)$pack->coins;
        $o->bonus_coins = 0;
        $o->pay_type = isset($opts['pay_type']) && $opts['pay_type'] !== '' ? $opts['pay_type'] : 'wxpay';
        $o->channel = isset($opts['channel']) ? (string)$opts['channel'] : '';
        $o->status = self::STATUS_PENDING;
        $o->refund_status = 0;
        $o->is_new_user = self::isFirstPay($user->id) ? 1 : 0;
        $o->deliver_no = '';
        $o->ip = isset($opts['ip']) ? (string)$opts['ip'] : '';
        $o->region = isset($opts['region']) ? (string)$opts['region'] : '';
        $o->remark = isset($opts['remark']) ? (string)$opts['remark'] : '';
        $o->paid_at = 0;
        $o->refund_at = 0;
        $o->created_at = $now;
        $o->updated_at = $now;
        $o->save(false);
        return $o;
    }

    /**
     * 前台下单（VIP 会员）：只建「待支付」订单，不加会员时长
     *
     * 与书币订单共用一张表，用 product_type='vip' 区分；
     * pack_id 记 0，vip_plan_id / vip_days / vip_scope 记录档位、时长与套餐类型
     * （novel 小说 VIP / browser 浏览器 VIP），支付成功后按 vip_scope 加到对应到期时间。
     *
     * @param User $user
     * @param VipPlan $plan
     * @param array $opts pay_type / channel / ip / region / remark
     * @return static
     */
    public static function createPendingVip(User $user, VipPlan $plan, array $opts = [])
    {
        $now = time();
        $o = new static();
        $o->order_no = self::genOrderNo();
        $o->channel_no = '';
        $o->user_id = (int)$user->id;
        $o->pack_id = 0;
        $o->product_title = $plan->productPrefix() . '·' . $plan->title;
        $o->product_type = 'vip';
        $testAmount = self::testAmountFor($user);
        $o->amount = $testAmount !== null ? $testAmount : $plan->price;
        $o->origin_amount = (float)$plan->origin_price > 0 ? $plan->origin_price : $plan->price;
        $o->coins = 0;
        $o->bonus_coins = 0;
        $o->pay_type = isset($opts['pay_type']) && $opts['pay_type'] !== '' ? $opts['pay_type'] : 'alipay';
        $o->channel = isset($opts['channel']) ? (string)$opts['channel'] : '';
        $o->status = self::STATUS_PENDING;
        $o->refund_status = 0;
        $o->is_new_user = self::isFirstPay($user->id) ? 1 : 0;
        $o->deliver_no = '';
        $o->ip = isset($opts['ip']) ? (string)$opts['ip'] : '';
        $o->region = isset($opts['region']) ? (string)$opts['region'] : '';
        $o->remark = isset($opts['remark']) ? (string)$opts['remark'] : '';
        $o->paid_at = 0;
        $o->refund_at = 0;
        $o->created_at = $now;
        $o->updated_at = $now;
        if ($o->hasAttribute('vip_plan_id')) {
            $o->vip_plan_id = (int)$plan->id;
            $o->vip_days = (int)$plan->days;
        }
        if ($o->hasAttribute('vip_scope')) {
            $o->vip_scope = $plan->scopeKey();
        }
        $o->save(false);
        return $o;
    }

    /** 是否会员订单 */
    public function isVipOrder()
    {
        return (string)$this->product_type === 'vip';
    }

    /** 这一单买的是哪套 VIP：novel / browser / mizhenhe（老订单没有该字段，按小说处理） */
    public function vipScope()
    {
        $s = $this->hasAttribute('vip_scope') ? (string)$this->vip_scope : '';
        return VipPlan::normalizeScope($s);
    }

    /**
     * 待支付 → 已支付（幂等）
     * 用带 status 条件的 updateAll 做状态机流转，重复点击/并发只会成功一次。
     *
     * @param string|null $payType 实际支付方式（不传沿用下单时选的）
     * @param string $tradeNo 第三方交易号（支付宝 trade_no），有则写入 channel_no
     * @return bool 本次调用是否真的完成了状态流转
     */
    public function markPaid($payType = null, $tradeNo = '')
    {
        $now = time();
        $tradeNo = trim((string)$tradeNo);
        $channelNo = $tradeNo !== ''
            ? mb_substr($tradeNo, 0, 64, 'UTF-8')
            : ($this->channel_no !== '' && $this->channel_no !== null ? $this->channel_no : self::genChannelNo());
        $rows = static::updateAll([
            'status' => self::STATUS_PAID,
            'pay_type' => $payType !== null && $payType !== '' ? $payType : $this->pay_type,
            'channel_no' => $channelNo,
            'deliver_no' => 'D' . date('YmdHis') . str_pad((string)mt_rand(0, 999), 3, '0', STR_PAD_LEFT),
            'is_new_user' => self::isFirstPay($this->user_id) ? 1 : 0,
            'paid_at' => $now,
            'updated_at' => $now,
        ], ['id' => (int)$this->id, 'status' => self::STATUS_PENDING]);

        return $rows === 1;
    }

    /**
     * 结算一笔已确认付款的订单：状态流转 + 加书币（同一事务，幂等）
     *
     * 只有支付网关验签通过后才会调用到这里。重复通知/重复回跳都安全：
     * markPaid 带 status=0 条件，抢不到就说明已经结算过，不会重复加币。
     *
     * @param RechargeOrder $order
     * @param string|null $payType 实际支付方式
     * @param string $tradeNo 第三方交易号
     * @return array [bool $ok, string $msg]
     */
    public static function settle($order, $payType = null, $tradeNo = '')
    {
        if (!$order) {
            return [false, 'order not found'];
        }
        if ((int)$order->status === self::STATUS_PAID) {
            return [true, 'already paid'];
        }
        if ((int)$order->status !== self::STATUS_PENDING) {
            return [false, 'order status ' . $order->status];
        }

        $tx = Yii::$app->db->beginTransaction();
        try {
            if (!$order->markPaid($payType, $tradeNo)) {
                // 并发下另一请求已完成结算
                $tx->commit();
                return [true, 'already paid'];
            }
            $user = User::findOne((int)$order->user_id);
            if ($user) {
                if ($order->isVipOrder()) {
                    // 会员订单：延长对应套餐的会员时长，不加书币
                    $days = $order->hasAttribute('vip_days') ? (int)$order->vip_days : 0;
                    $user->extendVipScope($order->vipScope(), $days, '开通「' . $order->getProductTitleText() . '」');
                } else {
                    $user->gainCoins((int)$order->coins, 'recharge', '充值「' . $order->getProductTitleText() . '」');
                }
            }
            $tx->commit();
            // 支付成功 → 入队一条百度「付费」转化（异步推送，绝不阻塞支付宝回调）
            ConvReport::onPay($order);
            return [true, 'paid'];
        } catch (\Throwable $e) {
            if ($tx->isActive) {
                $tx->rollBack();
            }
            Yii::error('recharge settle failed #' . $order->id . ': ' . $e->getMessage(), 'pay');
            return [false, 'server error'];
        }
    }

    /** 待支付 → 已关闭（幂等） */
    public function close()
    {
        return static::updateAll(
            ['status' => self::STATUS_CLOSED, 'updated_at' => time()],
            ['id' => (int)$this->id, 'status' => self::STATUS_PENDING]
        ) === 1;
    }

    /** 待支付订单超时时间（秒）：收银台超过这个时长未支付自动关单 */
    const PENDING_TTL = 1800;

    /**
     * 关闭某用户所有超时未支付的订单
     * @return int 关闭条数
     */
    public static function closeExpired($userId)
    {
        return static::updateAll(
            ['status' => self::STATUS_CLOSED, 'updated_at' => time()],
            [
                'and',
                ['user_id' => (int)$userId, 'status' => self::STATUS_PENDING],
                ['<', 'created_at', time() - self::PENDING_TTL],
            ]
        );
    }

    // ---------------- 统计 ----------------

    /** 日期分组表达式（统一按东八区切天） */
    protected static function dayExpr($col = 'created_at')
    {
        return Yii::$app->db->driverName === 'sqlite'
            ? new Expression("strftime('%Y-%m-%d', $col, 'unixepoch', '+8 hours')")
            : new Expression("DATE_FORMAT(FROM_UNIXTIME($col), '%Y-%m-%d')");
    }

    protected static function hourExpr($col = 'created_at')
    {
        return Yii::$app->db->driverName === 'sqlite'
            ? new Expression("CAST(strftime('%H', $col, 'unixepoch', '+8 hours') AS INTEGER)")
            : new Expression("HOUR(FROM_UNIXTIME($col))");
    }

    protected static function base($from, $to, array $codes = [], $mode = '')
    {
        $q = (new Query())->from(static::tableName())
            ->where(['>=', 'created_at', (int)$from])
            ->andWhere(['<=', 'created_at', (int)$to])
            ->andWhere(self::excludeTestUsersWhere());
        if ($codes) {
            // 只统计该渠道下的用户产生的订单
            $q->andWhere(['user_id' => (new Query())->select('id')
                ->from(User::tableName())->where(['channel_code' => $codes])]);
        }
        // App 形态（小说形式 / 浏览器模式）：订单没有形态字段，按 user_id 关联账号形态
        $mode = AppMode::normalize($mode);
        if ($mode !== '') {
            $q->andWhere(['user_id' => AppMode::userIds($mode)]);
        }
        return $q;
    }

    /**
     * 区间总览
     * @return array
     */
    public static function overview($from, $to, array $codes = [], $mode = '')
    {
        $row = static::base($from, $to, $codes, $mode)->select([
            'amount'       => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN amount ELSE 0 END), 0)'),
            'refund'       => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' AND refund_status = 2 THEN amount ELSE 0 END), 0)'),
            'coins'        => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN coins + bonus_coins ELSE 0 END), 0)'),
            'orders'       => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN 1 ELSE 0 END), 0)'),
            'users'        => new Expression('COUNT(DISTINCT CASE WHEN ' . self::PAID_SQL . ' THEN user_id END)'),
            'refund_users' => new Expression('COUNT(DISTINCT CASE WHEN ' . self::PAID_SQL . ' AND refund_status = 2 THEN user_id END)'),
            'new_users'    => new Expression('COUNT(DISTINCT CASE WHEN ' . self::PAID_SQL . ' AND is_new_user = 1 THEN user_id END)'),
            'max_amount'   => new Expression('COALESCE(MAX(CASE WHEN ' . self::PAID_SQL . ' THEN amount ELSE 0 END), 0)'),
            'all_orders'   => new Expression('COUNT(*)'),
            'pending'      => new Expression('COALESCE(SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END), 0)'),
            'closed'       => new Expression('COALESCE(SUM(CASE WHEN status = 3 THEN 1 ELSE 0 END), 0)'),
        ])->one();

        $amount = isset($row['amount']) ? (float)$row['amount'] : 0.0;
        $refund = isset($row['refund']) ? (float)$row['refund'] : 0.0;
        $orders = isset($row['orders']) ? (int)$row['orders'] : 0;
        $users = isset($row['users']) ? (int)$row['users'] : 0;

        // 复充：成功充值 ≥ 2 次的用户
        $freq = static::frequencies($from, $to, $codes, $mode);
        $repeatUsers = 0;
        foreach ($freq as $f) {
            if ((int)$f['c'] >= 2) {
                $repeatUsers++;
            }
        }

        $uq = User::find();
        if ($codes) {
            $uq->andWhere(['channel_code' => $codes]);
        }
        if ($mode !== '') {
            $w = AppMode::userWhere($mode);
            if ($w) {
                $uq->andWhere($w);
            }
        }
        $totalUsers = (int)$uq->count();
        $allOrders = isset($row['all_orders']) ? (int)$row['all_orders'] : 0;

        return [
            'amount'       => $amount,
            'refund'       => $refund,
            'net'          => $amount - $refund,
            'coins'        => isset($row['coins']) ? (int)$row['coins'] : 0,
            'orders'       => $orders,
            'users'        => $users,
            'refund_users' => isset($row['refund_users']) ? (int)$row['refund_users'] : 0,
            'new_users'    => isset($row['new_users']) ? (int)$row['new_users'] : 0,
            'max_amount'   => isset($row['max_amount']) ? (float)$row['max_amount'] : 0,
            'all_orders'   => $allOrders,
            'pending'      => isset($row['pending']) ? (int)$row['pending'] : 0,
            'closed'       => isset($row['closed']) ? (int)$row['closed'] : 0,
            'arppu'        => $users > 0 ? $amount / $users : 0,                 // 客单价
            'arpo'         => $orders > 0 ? $amount / $orders : 0,               // 单笔均价
            'pay_rate'     => $totalUsers > 0 ? $users / $totalUsers * 100 : 0,  // 付费率
            'repeat_rate'  => $users > 0 ? $repeatUsers / $users * 100 : 0,      // 复充率
            'success_rate' => $allOrders > 0 ? $orders / $allOrders * 100 : 0,   // 支付成功率
            'total_users'  => $totalUsers,
        ];
    }

    /**
     * 每个用户成功充值次数（复充率用）
     */
    public static function frequencies($from, $to, array $codes = [], $mode = '')
    {
        return static::base($from, $to, $codes, $mode)->select(['user_id', 'c' => new Expression('COUNT(*)')])
            ->andWhere(self::PAID_SQL)->groupBy('user_id')->all();
    }

    /**
     * 按天趋势，缺失日期补 0
     * @return array [['date'=>'2026-10-01','amount'=>..,'orders'=>..,'users'=>..,'refund'=>..], ...]
     */
    public static function daily($from, $to, array $codes = [], $mode = '')
    {
        $rows = static::base($from, $to, $codes, $mode)->select([
            'd' => static::dayExpr(),
            'amount' => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN amount ELSE 0 END), 0)'),
            'refund' => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' AND refund_status = 2 THEN amount ELSE 0 END), 0)'),
            'orders' => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN 1 ELSE 0 END), 0)'),
            'users'  => new Expression('COUNT(DISTINCT CASE WHEN ' . self::PAID_SQL . ' THEN user_id END)'),
        ])->groupBy(static::dayExpr())->orderBy(static::dayExpr())->all();

        $map = [];
        foreach ($rows as $r) {
            $map[$r['d']] = $r;
        }

        $out = [];
        $cursor = strtotime(date('Y-m-d', (int)$from));
        $last = strtotime(date('Y-m-d', (int)$to));
        while ($cursor <= $last) {
            $d = date('Y-m-d', $cursor);
            $out[] = [
                'date' => $d,
                'amount' => isset($map[$d]) ? (float)$map[$d]['amount'] : 0.0,
                'refund' => isset($map[$d]) ? (float)$map[$d]['refund'] : 0.0,
                'orders' => isset($map[$d]) ? (int)$map[$d]['orders'] : 0,
                'users' => isset($map[$d]) ? (int)$map[$d]['users'] : 0,
            ];
            $cursor = strtotime('+1 day', $cursor);
        }
        return $out;
    }

    /**
     * 24 小时时段分布
     */
    public static function hourly($from, $to, array $codes = [], $mode = '')
    {
        $rows = static::base($from, $to, $codes, $mode)->select([
            'h' => static::hourExpr(),
            'amount' => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN amount ELSE 0 END), 0)'),
            'orders' => new Expression('COALESCE(SUM(CASE WHEN ' . self::PAID_SQL . ' THEN 1 ELSE 0 END), 0)'),
        ])->andWhere(self::PAID_SQL)->groupBy(static::hourExpr())->all();

        $out = [];
        for ($i = 0; $i < 24; $i++) {
            $out[$i] = ['hour' => $i, 'amount' => 0.0, 'orders' => 0];
        }
        foreach ($rows as $r) {
            $h = (int)$r['h'];
            if (isset($out[$h])) {
                $out[$h]['amount'] = (float)$r['amount'];
                $out[$h]['orders'] = (int)$r['orders'];
            }
        }
        return array_values($out);
    }

    /**
     * 套餐 / 商品分布（按 pack_id 归并，套餐改名后不会拆成两行）
     */
    public static function byPack($from, $to, $limit = 10, array $codes = [], $mode = '')
    {
        $rows = static::base($from, $to, $codes, $mode)->select([
            'pack_id',
            'product_title' => new Expression('MAX(product_title)'),
            'amount' => new Expression('COALESCE(SUM(amount), 0)'),
            'orders' => new Expression('COUNT(*)'),
            'users'  => new Expression('COUNT(DISTINCT user_id)'),
        ])->andWhere(self::PAID_SQL)->groupBy('pack_id')
            ->orderBy(['amount' => SORT_DESC])->limit($limit)->all();

        $map = self::packTitleMap();
        foreach ($rows as &$r) {
            $pid = (int)$r['pack_id'];
            $r['product_title'] = ($pid > 0 && isset($map[$pid]) && $map[$pid] !== '')
                ? $map[$pid]
                : ($r['product_title'] !== '' ? $r['product_title'] : '未知商品');
            $r['amount'] = (float)$r['amount'];
            $r['orders'] = (int)$r['orders'];
            $r['users'] = (int)$r['users'];
        }
        unset($r);
        return $rows;
    }

    /**
     * 支付方式分布
     */
    public static function byPayType($from, $to, array $codes = [], $mode = '')
    {
        return static::base($from, $to, $codes, $mode)->select([
            'pay_type',
            'amount' => new Expression('COALESCE(SUM(amount), 0)'),
            'orders' => new Expression('COUNT(*)'),
        ])->andWhere(self::PAID_SQL)->groupBy('pay_type')->orderBy(['amount' => SORT_DESC])->all();
    }

    /**
     * 渠道分布
     */
    public static function byChannel($from, $to, $limit = 8, array $codes = [], $mode = '')
    {
        return static::base($from, $to, $codes, $mode)->select([
            'channel',
            'amount' => new Expression('COALESCE(SUM(amount), 0)'),
            'orders' => new Expression('COUNT(*)'),
        ])->andWhere(self::PAID_SQL)->groupBy('channel')->orderBy(['amount' => SORT_DESC])->limit($limit)->all();
    }

    /**
     * 订单状态分布（全部订单，含未支付）
     */
    public static function byStatus($from, $to, array $codes = [], $mode = '')
    {
        $rows = static::base($from, $to, $codes, $mode)->select([
            'status',
            'cnt' => new Expression('COUNT(*)'),
            'amount' => new Expression('COALESCE(SUM(amount), 0)'),
        ])->groupBy('status')->all();

        $out = [];
        foreach (self::statusList() as $k => $label) {
            $out[$k] = ['status' => $k, 'label' => $label, 'cnt' => 0, 'amount' => 0.0];
        }
        foreach ($rows as $r) {
            $s = (int)$r['status'];
            if (isset($out[$s])) {
                $out[$s]['cnt'] = (int)$r['cnt'];
                $out[$s]['amount'] = (float)$r['amount'];
            }
        }
        return array_values($out);
    }

    /**
     * 充值 TOP 用户
     */
    public static function topUsers($from, $to, $limit = 10, array $codes = [], $mode = '')
    {
        $rows = static::base($from, $to, $codes, $mode)->select([
            'user_id',
            'amount' => new Expression('COALESCE(SUM(amount), 0)'),
            'orders' => new Expression('COUNT(*)'),
            'last_paid' => new Expression('MAX(COALESCE(NULLIF(paid_at, 0), created_at))'),
        ])->andWhere(self::PAID_SQL)->groupBy('user_id')
            ->orderBy(['amount' => SORT_DESC])->limit($limit)->all();

        if (!$rows) {
            return [];
        }
        $users = User::find()->where(['id' => array_map('intval', array_column($rows, 'user_id'))])
            ->indexBy('id')->all();
        foreach ($rows as &$r) {
            $u = isset($users[$r['user_id']]) ? $users[$r['user_id']] : null;
            $r['username'] = $u ? $u->username : ('#' . $r['user_id']);
            $r['balance'] = $u ? (int)$u->coins : 0;
            $r['amount'] = (float)$r['amount'];
            $r['orders'] = (int)$r['orders'];
        }
        unset($r);
        return $rows;
    }

    /**
     * 某用户累计充值（记录页 / 详情页用）
     */
    public static function userTotal($userId)
    {
        $row = (new Query())->from(static::tableName())->where(['user_id' => (int)$userId])
            ->andWhere(self::PAID_SQL)->select([
                'amount' => new Expression('COALESCE(SUM(amount), 0)'),
                'orders' => new Expression('COUNT(*)'),
            ])->one();
        return [
            'amount' => isset($row['amount']) ? (float)$row['amount'] : 0.0,
            'orders' => isset($row['orders']) ? (int)$row['orders'] : 0,
        ];
    }
}
