<?php
namespace app\controllers\api;

use Yii;
use app\models\ApiToken;
use app\models\Channel;
use app\models\ConvReport;
use app\models\User;

/**
 * 米帧盒播放器专属账号接口（与书城 /api/auth/guest 完全分开）
 *
 *   POST /api/mizhenhe-user/register
 *   body: {device_id, platform, qd, bd_vid}
 *
 * 独立规则：
 *   - 登录账号  mz + 12 位 HMAC(设备号)，不复用书城的 s 前缀；
 *   - 展示昵称  「影友」+ 编号后 5 位（如 影友7A3F2），绝不叫「书友」；
 *   - reg_mode  mizhenhe，后台用户管理用米帧盒专属头像 + 「米帧盒」标签；
 *   - 签发正式账号令牌 is_guest=0，可直接 /api/pay/vip-create 下单支付。
 *
 * 继承 AuthController 只为复用设备号规范化 / 随机密码 / 登录留痕 / 令牌封装，
 * 不调用 actionGuest、不写书城设备账号体系。
 */
class MizhenheUserController extends AuthController
{
    protected $publicActions = ['register'];

    public function actionRegister()
    {
        $this->requirePost();

        $deviceId = $this->normalizeDeviceId($this->input('device_id', ''));
        if ($deviceId === '') {
            return $this->fail('缺少设备号或设备号不合法', 400);
        }

        $salt = (string)(Yii::$app->params['apiDeviceSalt'] ?? 'shiwenge-app-device');
        $username = 'mz' . substr(hash_hmac('sha256', $deviceId, $salt . '|mizhenhe'), 0, 12);
        $channel = Channel::normalize($this->input('qd', ''));

        $user = User::findByUsername($username);
        $created = 0;
        $plain = '';

        if ($user !== null) {
            if (!$user->isActive()) {
                return $this->fail($user->loginBlockedReason(), 403);
            }
        } else {
            // 与书城建号限流分开计数，避免互相挤占
            $rateKey = 'mzh_register_' . md5((string)Yii::$app->request->userIP);
            $count = (int)Yii::$app->cache->get($rateKey);
            if ($count >= 30) {
                return $this->fail('操作过于频繁，请稍后再试', 429);
            }

            $plain = $this->randomPassword();
            $user = new User();
            $user->username = $username;
            $user->setPassword($plain);
            $user->generateAuthKey();
            $user->coins = 0;
            $user->created_at = time();
            if ($user->hasAttribute('reg_mode')) {
                $user->reg_mode = User::REG_MODE_MIZHENHE;
            }
            if ($channel !== '' && $user->hasAttribute('channel_code')) {
                $user->channel_code = $channel;
            }
            if (!$user->save(false)) {
                return $this->fail('账号创建失败，请重试', 500);
            }
            Yii::$app->cache->set($rateKey, $count + 1, 3600);
            $created = 1;
            ConvReport::onRegister($user, (string)$this->input('bd_vid', ''), $deviceId, true);
        }

        // 老账号回访 / 渠道逻辑改写过 reg_mode 时纠正回米帧盒形态，
        // 保证后台头像与昵称不会被判成小说号。
        if ($user->hasAttribute('reg_mode') && (string)$user->reg_mode !== User::REG_MODE_MIZHENHE) {
            $user->reg_mode = User::REG_MODE_MIZHENHE;
            $user->save(false, ['reg_mode']);
        }

        $this->recordAppLogin($user);
        $token = ApiToken::issue($user, (string)$this->input('platform', 'ios'), false);
        $payload = $this->tokenPayload($token, $user, false);
        $payload['app'] = 'mizhenhe';
        $payload['device_account'] = [
            'username' => (string)$user->username,
            'password' => $plain,
            'created' => $created,
        ];
        $payload['reco'] = null;
        return $this->ok($payload, $created ? '米帧盒账号已创建' : '登录成功');
    }
}
