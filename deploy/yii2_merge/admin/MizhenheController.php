<?php
namespace app\controllers\admin;

use Yii;

/**
 * 后台左侧菜单「🎬 米帧盒播放器」
 *
 * 沿用 AdminBaseController 的登录与角色校验，把米帧盒的渠道展示 /
 * 原后台接口接入面板嵌进 /site 同一套布局，实现「一个后台、一次登录」。
 * 本控制器不读写渠道表，不触碰任何在跑量的渠道配置。
 */
class MizhenheController extends AdminBaseController
{
    public function actionIndex()
    {
        return $this->render('index');
    }
}
