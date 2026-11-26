<?php
namespace app\models;

use Yii;
use yii\base\NotSupportedException;
use yii\db\ActiveRecord;
use yii\web\IdentityInterface;

/**
 * 用户模型（含书币钱包）
 *
 * @property int $id
 * @property string $username
 * @property string $password_hash
 * @property string $auth_key
 * @property int $is_admin
 * @property int $coins
 * @property int $created_at
 * @property int $status       0 正常 / 1 锁定 / 2 注销
 * @property string $remark
 * @property string $reg_ip        注册 IP
 * @property int $last_login_at
 * @property string $last_login_ip
 * @property string $last_login_region
 * @property string $last_device
 * @property int $login_count
 * @property int $updated_at
 * @property string $reg_mode  注册形态：novel（书友）/ browser（用户）
 * @property string $app_pkg        客户端包名（com.starworldxyz.beaugood）
 * @property string $app_ver        客户端版本名（1.3.3）
 * @property string $device_os      手机系统（安卓 16 / iOS 16.5）
 * @property string $device_model   机型（OPPO PLK110）
 * @property int $app_info_at       包信息最后采集时间
 */
class User extends ActiveRecord implements IdentityInterface
{
    /** 账号状态 */
    const STATUS_NORMAL = 0;
    const STATUS_LOCKED = 1;
    const STATUS_CANCELLED = 2;

    public static function tableName()
    {
        return '{{%user}}';
    }

    /**
     * 新号入库时自动补记注册 IP
     *
     * 网站注册（SignupForm）、App 注册、App 一键登录建号（AuthController）都走
     * ActiveRecord 的 insert，所以在模型层统一记一次即可，不必逐个入口改。
     */
    public function beforeSave($insert)
    {
        if (!parent::beforeSave($insert)) {
            return false;
        }
        if ($insert && $this->hasAttribute('reg_ip') && (string)$this->reg_ip === '') {
            $ip = '';
            if (Yii::$app->has('request') && Yii::$app->request instanceof \yii\web\Request) {
                $ip = (string)Yii::$app->request->userIP;
            }
            if ($ip !== '') {
                $this->reg_ip = mb_substr($ip, 0, 45);
            }
        }
        // 新号没显式指定注册形态时，按渠道配置兜底判定（App 建号会在控制器里先写好）
        if ($insert && $this->hasAttribute('reg_mode') && (string)$this->reg_mode === '') {
            $mode = self::modeOfChannel(
                $this->hasAttribute('channel_code') ? (string)$this->channel_code : ''
            );
            $this->reg_mode = $mode;
        }
        return true;
    }

    /**
     * 按 id 取身份
     *
     * 锁定 / 注销的账号一律返回 null：网站会话会被判为游客，App 令牌也随之下线，
     * 这样后台一锁定就立即生效，不用等用户下次登录。
     */
    public static function findIdentity($id)
    {
        $user = static::findOne($id);
        if ($user !== null && !$user->isActive()) {
            return null;
        }
        return $user;
    }

    public static function findIdentityByAccessToken($token, $type = null)
    {
        throw new NotSupportedException('暂不支持令牌登录');
    }

    public function getId()
    {
        return $this->getPrimaryKey();
    }

    public function getAuthKey()
    {
        return $this->auth_key;
    }

    public function validateAuthKey($authKey)
    {
        return $this->getAuthKey() === $authKey;
    }

    public static function findByUsername($username)
    {
        return static::findOne(['username' => $username]);
    }

    public function validatePassword($password)
    {
        return Yii::$app->security->validatePassword($password, $this->password_hash);
    }

    public function setPassword($password)
    {
        $this->password_hash = Yii::$app->security->generatePasswordHash($password);
    }

    public function generateAuthKey()
    {
        $this->auth_key = Yii::$app->security->generateRandomString();
    }

    public function isAdmin()
    {
        return (int)$this->is_admin === 1;
    }

    // ------------------------------------------------------------------
    // 管理员角色（m261028_220000_admin_role 之后才有 admin_role 列）
    // ------------------------------------------------------------------

    const ROLE_SUPER = 'super';
    const ROLE_OPS = 'ops';
    const ROLE_CHANNEL = 'channel';
    const ROLE_VIEWER = 'viewer';

    /** 角色 => 中文名 */
    public static function roleList()
    {
        return [
            self::ROLE_SUPER => '超级管理员',
            self::ROLE_OPS => '运营专员',
            self::ROLE_CHANNEL => '渠道管理员',
            self::ROLE_VIEWER => '只读账号',
        ];
    }

    /** 每个角色能进的后台模块（路由前缀） */
    public static function rolePerms()
    {
        return [
            self::ROLE_SUPER => ['*'],
            self::ROLE_OPS => [
                'admin/stat', 'admin/recharge', 'admin/book', 'admin/user',
                'admin/region', 'admin/pack', 'admin/pay', 'admin/whitelist', 'admin/channel', 'admin/browser',
                'admin/conv', 'admin/cover-tpl', 'admin/deduct',
            ],
            // 渠道管理员：数据统计（含「进入百度管理后台」）、渠道管理 + 渠道展示、
            // 充值记录、用户管理、回传管理（含推广账号设置 / 百度转化回传 / 回传记录 / 推广扣量）
            self::ROLE_CHANNEL => [
                'admin/stat', 'admin/channel', 'admin/recharge', 'admin/user', 'admin/conv',
                'admin/deduct',
            ],
            self::ROLE_VIEWER => ['admin/stat'],
        ];
    }

    /** 当前账号角色标识；老数据没有 admin_role 时按超管处理，避免锁死后台 */
    public function getRole()
    {
        $r = $this->hasAttribute('admin_role') ? (string)$this->admin_role : '';
        return isset(self::roleList()[$r]) ? $r : self::ROLE_SUPER;
    }

    public function getRoleName()
    {
        return self::roleList()[$this->getRole()] ?? '管理员';
    }

    public function isSuperAdmin()
    {
        return $this->isAdmin() && $this->getRole() === self::ROLE_SUPER;
    }

    /** 是否有权访问某个后台路由 */
    public function canAccessRoute($route)
    {
        if (!$this->isAdmin()) {
            return false;
        }
        $perms = self::rolePerms()[$this->getRole()] ?? [];
        if (in_array('*', $perms, true)) {
            return true;
        }
        foreach ($perms as $p) {
            if (strpos((string)$route, $p) === 0) {
                return true;
            }
        }
        return false;
    }


    // ------------------------------------------------------------------
    // 账号状态（m261026_120000_user_admin 之后才有 status 列）
    // ------------------------------------------------------------------

    /** 账号是否可用（迁移没跑时没有 status 列，按正常处理，保证网站不挂） */
    public function isActive()
    {
        if (!$this->hasAttribute('status')) {
            return true;
        }
        return (int)$this->status === self::STATUS_NORMAL;
    }

    public function isLocked()
    {
        return $this->hasAttribute('status') && (int)$this->status === self::STATUS_LOCKED;
    }

    public function isCancelled()
    {
        return $this->hasAttribute('status') && (int)$this->status === self::STATUS_CANCELLED;
    }

    /** 状态中文 */
    public function getStatusText()
    {
        if ($this->isLocked()) {
            return '已锁定';
        }
        if ($this->isCancelled()) {
            return '已注销';
        }
        return '正常';
    }

    /** 状态徽章样式（site.css 的 .st-*） */
    public function getStatusBadgeClass()
    {
        if ($this->isLocked()) {
            return 'st-pending';
        }
        if ($this->isCancelled()) {
            return 'st-refunded';
        }
        return 'st-paid';
    }

    /** 状态列表：后台筛选下拉用 */
    public static function statusList()
    {
        return [
            self::STATUS_NORMAL => '正常',
            self::STATUS_LOCKED => '已锁定',
            self::STATUS_CANCELLED => '已注销',
        ];
    }

    /**
     * 展示用昵称
     *
     * App 的一键登录账号是 s + 12 位十六进制（由设备号推算），
     * 界面上显示成「书友XXXXX」（小说形态包）或「用户XXXXX」（浏览器形态包），
     * 与 App 内保持一致；形态取自账号注册时所属渠道在「渠道展示」里配的 app_mode。
     */
    public function getDisplayName()
    {
        $name = (string)$this->username;
        // 书城设备号 s+12hex；米帧盒独立编号 mz+12hex，绝不叫「书友」
        if (preg_match('/^(?:s|mz)[0-9a-f]{12}$/i', $name)) {
            return $this->getDisplayNamePrefix() . strtoupper(substr($name, -5));
        }
        return $name;
    }

    /** 米帧盒播放器专属注册形态（不复用 novel / browser 两套） */
    const REG_MODE_MIZHENHE = 'mizhenhe';

    /**
     * 昵称前缀：米帧盒「影友」/ 浏览器「用户」/ 小说「书友」
     * @return string
     */
    public function getDisplayNamePrefix()
    {
        $mode = $this->getRegMode();
        if ($mode === self::REG_MODE_MIZHENHE) {
            return '影友';
        }
        return $mode === ChannelDisplay::APP_MODE_BROWSER ? '用户' : '书友';
    }

    /**
     * 账号的注册形态（novel / browser）
     *
     * 优先用建号时固化的 reg_mode；老账号没有这个值时，回落到所属渠道当前的 app_mode；
     * 没有渠道归属的按小说形态处理，保证老包显示不变。
     * @return string
     */
    public function getRegMode()
    {
        $mode = $this->hasAttribute('reg_mode') ? strtolower(trim((string)$this->reg_mode)) : '';
        if ($mode === self::REG_MODE_MIZHENHE
            || $mode === ChannelDisplay::APP_MODE_NOVEL
            || $mode === ChannelDisplay::APP_MODE_BROWSER) {
            return $mode;
        }
        return self::modeOfChannel($this->hasAttribute('channel_code') ? (string)$this->channel_code : '');
    }

    /** 默认头像（小说 / 浏览器两套共用） */
    const AVATAR_URL = '/img/avatar-default.png';
    /** 米帧盒播放器专属头像：后台用户管理一眼能认出来 */
    const AVATAR_URL_MIZHENHE = '/img/avatar-mizhenhe.png';

    /**
     * 头像地址（后台列表、用户详情用）
     * @return string
     */
    public function getAvatarUrl()
    {
        return $this->getRegMode() === self::REG_MODE_MIZHENHE
            ? self::AVATAR_URL_MIZHENHE : self::AVATAR_URL;
    }

    /**
     * 渠道 => 注册形态
     *
     * 渠道号为空时按当前请求的渠道判断（网站带 qd 参数进来的注册），再兜底小说形态。
     *
     * @param string $code 一级/二级渠道号
     * @return string novel|browser
     */
    public static function modeOfChannel($code = '')
    {
        static $cache = [];
        $code = trim((string)$code);
        if ($code === '') {
            try {
                $code = ChannelDisplay::currentCode();
            } catch (\Throwable $e) {
                $code = '';
            }
        }
        $key = $code === '' ? '__none__' : $code;
        if (isset($cache[$key])) {
            return $cache[$key];
        }
        $mode = ChannelDisplay::APP_MODE_NOVEL;
        try {
            $cfg = ChannelDisplay::config($code !== '' ? $code : null);
            if (isset($cfg['app_mode']) && $cfg['app_mode'] === ChannelDisplay::APP_MODE_BROWSER) {
                $mode = ChannelDisplay::APP_MODE_BROWSER;
            }
        } catch (\Throwable $e) {
            $mode = ChannelDisplay::APP_MODE_NOVEL;
        }
        $cache[$key] = $mode;
        return $mode;
    }

    /** 昵称首字（当头像用） */
    public function getAvatarChar()
    {
        $name = $this->getDisplayName();
        return mb_substr($name !== '' ? $name : $this->getDisplayNamePrefix(), 0, 1);
    }

    /**
     * 登录被拒绝时的原因（账号正常返回空字符串）
     * @return string
     */
    public function loginBlockedReason()
    {
        if ($this->isLocked()) {
            return '该账号已被锁定，请联系客服处理';
        }
        if ($this->isCancelled()) {
            return '该账号已注销，无法登录';
        }
        return '';
    }

    /**
     * 记录一次登录：最近登录时间 / IP / 设备 / 地区 + 登录次数 + 登录日志
     *
     * @param string $ip
     * @param string $device 形如「Android · Chrome」「App · iOS」
     * @param string $region
     * @return void
     */
    public function recordLogin($ip = '', $device = '', $region = '')
    {
        if (!$this->hasAttribute('last_login_at')) {
            return;
        }
        // 调用方没给地区时自己反查（走本地 ip2region 库，几毫秒，不联网）
        if ($region === '' && $ip !== '') {
            try {
                $loc = RegionBlock::lookupLocation($ip);
                $region = trim($loc['province'] . ($loc['city'] !== '' ? ' ' . $loc['city'] : ''));
            } catch (\Throwable $e) {
                $region = '';
            }
        }
        $this->last_login_at = time();
        $this->last_login_ip = mb_substr((string)$ip, 0, 45);
        $this->last_device = mb_substr((string)$device, 0, 64);
        $this->last_login_region = mb_substr((string)$region, 0, 64);
        $this->login_count = (int)$this->login_count + 1;
        $this->save(false, ['last_login_at', 'last_login_ip', 'last_device', 'last_login_region', 'login_count']);

        UserLog::add(
            $this->id,
            UserLog::TYPE_LOGIN,
            ($device !== '' ? $device . ' ' : '') . 'IP ' . ($ip !== '' ? $ip : '-'),
            0,
            $ip
        );
    }

    /**
     * 记录 App 包信息（包名 / 版本号 / 系统 / 机型）
     *
     * 后台用户列表要显示「包信息」三行，靠这里落库。
     * App 每次请求都会带 X-App-* 请求头（老客户端只能从 UA 拿到系统 + 机型），
     * 为避免每个请求都写库：值没变且 30 分钟内更新过就跳过。
     *
     * @param array $info ['pkg'=>'', 'ver'=>'', 'os'=>'', 'model'=>'', 'src'=>'header|channel']
     * @return void
     */
    public function recordAppInfo(array $info)
    {
        if (!$this->hasAttribute('app_pkg')) {
            return;
        }
        $src = trim((string)($info['src'] ?? ''));
        $map = [
            'app_pkg' => 'pkg',
            'app_ver' => 'ver',
            'device_os' => 'os',
            'device_model' => 'model',
        ];
        $fields = [];
        $changed = false;
        foreach ($map as $col => $key) {
            $val = trim((string)($info[$key] ?? ''));
            if ($val === '') {
                continue;
            }
            // app_pkg 特殊处理：按渠道推算出来的包名不能覆盖客户端真报过的
            // （老客户端没带 X-App-Pkg 时只能按渠道包兜底，那只是猜的）
            if ($col === 'app_pkg' && $src === 'channel') {
                $cur = trim((string)$this->app_pkg);
                if ($cur !== '' && $cur !== $val) {
                    continue;
                }
            }
            if ($val !== (string)$this->$col) {
                $this->$col = $val;
                $fields[] = $col;
                $changed = true;
            }
        }
        // 包名来源：header=客户端真报 / channel=按渠道绑定的包推的
        if ($this->hasAttribute('app_pkg_src') && $src !== '' && $src !== (string)$this->app_pkg_src) {
            $this->app_pkg_src = $src;
            $fields[] = 'app_pkg_src';
            $changed = true;
        }
        $last = (int)$this->app_info_at;
        if (!$changed && $last > 0 && time() - $last < 1800) {
            return;
        }
        $this->app_info_at = time();
        $fields[] = 'app_info_at';
        try {
            $this->save(false, $fields);
        } catch (\Throwable $e) {
            // 采集失败不能影响接口
        }
    }

    /**
     * 包信息三行：[包名, 版本(系统), 机型]，后台列表直接渲染
     *
     * @return array
     */
    public function appInfoLines()
    {
        return AppInfo::lines($this);
    }

    /**
     * 是否解锁了某章
     */
    public function hasUnlocked($bookId, $chapterIdx)
    {
        return UnlockRecord::find()->where([
            'user_id' => $this->id, 'book_id' => $bookId, 'chapter_idx' => $chapterIdx,
        ])->exists();
    }

    /**
     * 消费书币（含解锁记录可选、账本），在事务中调用
     * @return bool
     */
    public function spendCoins($amount, $note)
    {
        $amount = (int)$amount;
        if ($this->coins < $amount) {
            return false;
        }
        $this->coins -= $amount;
        if (!$this->save(false, ['coins'])) {
            return false;
        }
        Ledger::add($this->id, 'spend', $amount, $this->coins, $note);
        return true;
    }

    /**
     * 收入书币（充值/后台调整），写账本
     */
    public function gainCoins($amount, $type, $note)
    {
        $this->coins += (int)$amount;
        $this->save(false, ['coins']);
        Ledger::add($this->id, $type, (int)$amount, $this->coins, $note);
    }

    // ---------------- VIP 会员 ----------------
    //
    // 三套完全独立的会员：
    //   novel    → vip_expire_at            小说 VIP（付费章节 / VIP 封面）
    //   browser  → browser_vip_expire_at    浏览器 VIP（浏览首页的全部功能）
    //   mizhenhe → mzh_vip_expire_at        米帧盒 VIP（米帧盒播放器原生 App / H5）
    // 买哪套只延长哪套，互不影响。字段缺省时按「非会员」处理，不会整站报错。

    /** scope → 到期时间字段 */
    protected function vipField($scope)
    {
        $s = VipPlan::normalizeScope($scope);
        if ($s === VipPlan::SCOPE_MIZHENHE) {
            return 'mzh_vip_expire_at';
        }
        return $s === VipPlan::SCOPE_BROWSER ? 'browser_vip_expire_at' : 'vip_expire_at';
    }

    /**
     * 是否指定类型的 VIP（未过期）
     */
    public function isVipScope($scope = VipPlan::SCOPE_NOVEL)
    {
        if ((int)$this->is_admin === 1) {
            return false; // 后台账号不参与前台会员逻辑
        }
        $field = $this->vipField($scope);
        if (!$this->hasAttribute($field)) {
            return false;
        }
        return (int)$this->$field > time();
    }

    /** 指定类型的会员到期时间戳（0 = 从未开通） */
    public function vipExpireAtScope($scope = VipPlan::SCOPE_NOVEL)
    {
        $field = $this->vipField($scope);
        return $this->hasAttribute($field) ? (int)$this->$field : 0;
    }

    /** 指定类型的会员剩余天数（未开通 / 已过期 = 0） */
    public function vipDaysLeftScope($scope = VipPlan::SCOPE_NOVEL)
    {
        $expire = $this->vipExpireAtScope($scope);
        if ($expire <= time()) {
            return 0;
        }
        return (int)ceil(($expire - time()) / 86400);
    }

    /**
     * 是否 VIP（未过期）
     * 迁移没跑（没有 vip_expire_at 字段）时一律按非会员处理，避免整站报错。
     */
    public function isVip()
    {
        return $this->isVipScope(VipPlan::SCOPE_NOVEL);
    }

    /** 是否浏览器 VIP（未过期） */
    public function isBrowserVip()
    {
        return $this->isVipScope(VipPlan::SCOPE_BROWSER);
    }

    /** 会员到期时间戳（0 = 从未开通） */
    public function vipExpireAt()
    {
        return $this->vipExpireAtScope(VipPlan::SCOPE_NOVEL);
    }

    /** 浏览器会员到期时间戳（0 = 从未开通） */
    public function browserVipExpireAt()
    {
        return $this->vipExpireAtScope(VipPlan::SCOPE_BROWSER);
    }

    /** 会员剩余天数（未开通 / 已过期 = 0） */
    public function vipDaysLeft()
    {
        return $this->vipDaysLeftScope(VipPlan::SCOPE_NOVEL);
    }

    /** 浏览器会员剩余天数 */
    public function browserVipDaysLeft()
    {
        return $this->vipDaysLeftScope(VipPlan::SCOPE_BROWSER);
    }

    /**
     * 开通 / 续费 VIP（小说）
     *
     * 续费从「当前到期时间」往后顺延，已过期的从「现在」开始算，
     * 这样连买两次就是累计时长，不会把剩余天数吃掉。
     *
     * @param int $days 天数
     * @param string $note 账本备注（会员不扣币，仅记录流水方便对账）
     * @return int 新的到期时间戳
     */
    public function extendVip($days, $note = '')
    {
        return $this->extendVipScope(VipPlan::SCOPE_NOVEL, $days, $note);
    }

    /** 开通 / 续费浏览器 VIP */
    public function extendBrowserVip($days, $note = '')
    {
        return $this->extendVipScope(VipPlan::SCOPE_BROWSER, $days, $note);
    }

    /**
     * 开通 / 续费指定类型的 VIP
     *
     * @param string $scope novel / browser
     * @param int $days
     * @param string $note
     * @return int 新的到期时间戳
     */
    public function extendVipScope($scope, $days, $note = '')
    {
        $field = $this->vipField($scope);
        $days = (int)$days;
        if ($days <= 0) {
            return $this->vipExpireAtScope($scope);
        }
        if (!$this->hasAttribute($field)) {
            return 0;
        }
        $now = time();
        $base = (int)$this->$field > $now ? (int)$this->$field : $now;
        $this->$field = $base + $days * 86400;
        $this->save(false, [$field]);
        // 会员不涉及书币变动，账本记 0 币一条，方便后台看到「何时开的会员」
        try {
            $s = VipPlan::normalizeScope($scope);
            $label = $s === VipPlan::SCOPE_BROWSER ? '浏览器VIP'
                : ($s === VipPlan::SCOPE_MIZHENHE ? '米帧盒VIP' : 'VIP');
            Ledger::add($this->id, 'vip', 0, (int)$this->coins, $note !== '' ? $note : ('开通 ' . $label . ' ' . $days . ' 天'));
        } catch (\Throwable $e) {
            // 账本写失败不影响会员生效
        }
        return (int)$this->$field;
    }
}
