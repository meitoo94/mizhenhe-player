<?php
namespace app\controllers;

use Yii;
use yii\web\Controller;
use yii\web\Response;

/**
 * 米帧盒后台单点登录鉴权端点（只给 nginx auth_request 的内部子请求调用）
 *
 * 复用 /site 后台的 PHPSESSID 会话：已登录管理员返回 200，未登录返回 403。
 * 不渲染页面、不重定向、不改任何业务数据，避免 auth_request 收到 302。
 */
class MizhenheAuthController extends Controller
{
    public $layout = false;
    public $enableCsrfValidation = false;

    public function actionIndex()
    {
        $resp = Yii::$app->response;
        $resp->format = Response::FORMAT_RAW;

        $user = Yii::$app->user;
        $identity = $user->identity;
        $ok = !$user->isGuest
            && $identity !== null
            && method_exists($identity, 'isAdmin')
            && $identity->isAdmin();

        $resp->statusCode = $ok ? 200 : 403;
        $resp->content = $ok ? 'ok' : 'forbidden';
        return $resp;
    }
}
