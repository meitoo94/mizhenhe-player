<?php
namespace app\models;

use yii\db\ActiveRecord;

/**
 * VIP 会员档位（月卡 / 季卡 / 年卡）
 *
 * 三套完全独立的套餐，靠 scope 区分：
 *   novel    小说 VIP —— 书城「我的」会员卡、VIP 封面、付费章节免币阅读
 *   browser  浏览器 VIP —— 浏览首页所有功能（私密相册 / 文件隐藏 / 密码锁 …）
 *   mizhenhe 米帧盒 VIP —— 米帧盒播放器原生 App / H5 会员页专用（2026-11-26 新增）
 * 各套的档位互不干扰，改米帧盒套餐不会动到小说 / 浏览器在售档位。
 *
 * 后台「支付相关管理 → 小说VIP套餐 / 浏览器VIP套餐 / 米帧盒VIP套餐」可改名称、天数、原价、折扣价、角标与上下架。
 * 前台只取 is_active=1 的档位，按 sort 升序展示。
 *
 * @property int $id
 * @property string $scope       novel / browser / mizhenhe
 * @property string $code        month / quarter / year（固定三档，便于代码里定向取用）
 * @property string $title       展示名（月卡 / 季卡 / 年卡）
 * @property int $days           这一档开通多少天
 * @property string $origin_price 原价（划线价）
 * @property string $price       折扣价（实际支付）
 * @property string $badge       右上角角标文案（空 = 不显示）
 * @property int $is_active      1 上架 / 0 下架
 * @property int $sort
 * @property int $updated_at
 */
class VipPlan extends ActiveRecord
{
    /** 小说 VIP：解锁付费章节 / VIP 封面 */
    const SCOPE_NOVEL = 'novel';
    /** 浏览器 VIP：解锁浏览首页的全部功能 */
    const SCOPE_BROWSER = 'browser';
    /** 米帧盒 VIP：米帧盒播放器原生 App / H5 专用，独立配置、独立权益 */
    const SCOPE_MIZHENHE = 'mizhenhe';

    public static function tableName()
    {
        return '{{%vip_plan}}';
    }

    /** 两套套餐的展示名 */
    public static function scopeNames()
    {
        return [
            self::SCOPE_NOVEL => '小说VIP套餐',
            self::SCOPE_BROWSER => '浏览器VIP套餐',
            self::SCOPE_MIZHENHE => '米帧盒VIP套餐',
        ];
    }

    /** 把任意输入规整成合法 scope（不认识的按小说处理） */
    public static function normalizeScope($scope)
    {
        $s = (string)$scope;
        return in_array($s, [self::SCOPE_NOVEL, self::SCOPE_BROWSER, self::SCOPE_MIZHENHE], true)
            ? $s
            : self::SCOPE_NOVEL;
    }

    /** 这个 scope 叫什么名字 */
    public static function scopeName($scope)
    {
        $names = self::scopeNames();
        $s = self::normalizeScope($scope);
        return $names[$s];
    }

    public function rules()
    {
        return [
            [['title', 'days', 'price'], 'required', 'message' => '{attribute} 不能为空'],
            [['days', 'sort', 'is_active'], 'integer'],
            [['days'], 'integer', 'min' => 1, 'max' => 36500],
            [['origin_price', 'price'], 'number', 'min' => 0],
            [['price'], 'number', 'min' => 0.01],
            [['title', 'badge'], 'string', 'max' => 40],
            [['code', 'scope'], 'string', 'max' => 20],
            [['is_active'], 'default', 'value' => 1],
            [['sort'], 'default', 'value' => 0],
            [['scope'], 'default', 'value' => self::SCOPE_NOVEL],
        ];
    }

    public function attributeLabels()
    {
        return [
            'title' => '档位名称',
            'days' => '开通天数',
            'origin_price' => '原价(元)',
            'price' => '折扣价(元)',
            'badge' => '角标文案',
            'is_active' => '上架',
            'sort' => '排序',
            'scope' => '套餐类型',
        ];
    }

    /** 上架中的档位（前台展示顺序） */
    public static function activePlans($scope = self::SCOPE_NOVEL)
    {
        return static::find()
            ->where(['scope' => self::normalizeScope($scope), 'is_active' => 1])
            ->orderBy(['sort' => SORT_ASC, 'days' => SORT_ASC])
            ->all();
    }

    /** 后台列表：该 scope 的三档全查出来（含下架） */
    public static function allPlans($scope = self::SCOPE_NOVEL)
    {
        return static::find()
            ->where(['scope' => self::normalizeScope($scope)])
            ->orderBy(['sort' => SORT_ASC, 'days' => SORT_ASC])
            ->all();
    }

    /** 某一档（下架的也算，历史订单对账要用） */
    public static function byCode($code, $scope = self::SCOPE_NOVEL)
    {
        return static::find()
            ->where(['scope' => self::normalizeScope($scope), 'code' => (string)$code])
            ->one();
    }

    /** 本档位属于哪套套餐 */
    public function scopeKey()
    {
        return self::normalizeScope($this->scope);
    }

    /** 是不是浏览器 VIP 档位 */
    public function isBrowserScope()
    {
        return $this->scopeKey() === self::SCOPE_BROWSER;
    }

    /** 前台/订单里用的套餐前缀文案 */
    public function productPrefix()
    {
        if ($this->scopeKey() === self::SCOPE_MIZHENHE) {
            return '米帧盒VIP';
        }
        return $this->isBrowserScope() ? '浏览器VIP' : 'VIP会员';
    }

    /** 价格文案：29 / 29.9（去掉多余的 0） */
    public function priceText()
    {
        return self::fmt($this->price);
    }

    public function originPriceText()
    {
        return self::fmt($this->origin_price);
    }

    /** 日均单价（VIP 封面卡片上「¥0.28/天」那种写法） */
    public function perDayText()
    {
        $days = (int)$this->days;
        if ($days <= 0) {
            return self::fmt($this->price);
        }
        $per = (float)$this->price / $days;
        // 终身卡这类超长天数，两位小数会显示成 0.00，给个下限文案
        if ($per > 0 && $per < 0.01) {
            return '<0.01';
        }
        return number_format($per, 2, '.', '');
    }

    /** 折扣：有原价且高于现价时才算，返回如「省 40 元」；否则空串 */
    public function savingText()
    {
        $origin = (float)$this->origin_price;
        $price = (float)$this->price;
        if ($origin <= $price) {
            return '';
        }
        return '省 ' . self::fmt($origin - $price) . ' 元';
    }

    protected static function fmt($v)
    {
        return rtrim(rtrim(number_format((float)$v, 2, '.', ''), '0'), '.');
    }
}
