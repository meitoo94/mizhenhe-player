<?php
namespace app\controllers\admin;

use Yii;
use app\models\VipPlan;
use app\models\VipPlanChannel;

/**
 * 后台：VIP 套餐配置（小说 / 浏览器两套）
 *
 * 两套套餐各三档（月卡 / 季卡 / 年卡），页面通过 ?scope=novel|browser 切换：
 *   novel   小说VIP套餐 —— 付费章节、VIP 封面
 *   browser 浏览器VIP套餐 —— 浏览首页的所有功能（私密相册 / 文件隐藏 / 密码锁 …）
 *
 * 两套价格默认一致，之后可以各改各的；「按小说套餐同步价格」一键把小说价复制到浏览器。
 */
class VipController extends AdminBaseController
{
    /** 当前正在编辑哪套套餐 */
    protected function currentScope()
    {
        return VipPlan::normalizeScope(Yii::$app->request->get('scope', VipPlan::SCOPE_NOVEL));
    }

    public function actionIndex()
    {
        $scope = $this->currentScope();
        $plans = VipPlan::allPlans($scope);
        return $this->render('index', [
            'plans' => $plans,
            'scope' => $scope,
            'scopeNames' => VipPlan::scopeNames(),
            'channelRows' => VipPlanChannel::channelRows($scope),
        ]);
    }

    /**
     * 批量保存该 scope 的三档
     */
    public function actionSave()
    {
        $post = Yii::$app->request->post();
        if (!Yii::$app->request->isPost) {
            return $this->redirect(['index']);
        }

        $scope = VipPlan::normalizeScope(isset($post['scope']) ? $post['scope'] : VipPlan::SCOPE_NOVEL);
        $rows = (array)($post['plan'] ?? []);
        $saved = 0;
        $errors = [];

        $db = Yii::$app->db;
        $tx = $db->beginTransaction();
        try {
            foreach ($rows as $id => $p) {
                // 只允许改当前 scope 的档位，防止表单被改串套餐
                $model = VipPlan::find()
                    ->where(['id' => (int)$id, 'scope' => $scope])
                    ->one();
                if (!$model) {
                    continue;
                }
                $title = trim((string)($p['title'] ?? ''));
                $days = (int)($p['days'] ?? 0);
                $price = (float)($p['price'] ?? 0);
                $origin = (float)($p['origin_price'] ?? 0);

                if ($title === '') {
                    $errors[] = '「' . $model->title . '」名称不能为空';
                    continue;
                }
                if ($days < 1 || $days > 36500) {
                    $errors[] = '「' . $title . '」天数需在 1~36500 之间';
                    continue;
                }
                if ($price <= 0) {
                    $errors[] = '「' . $title . '」折扣价必须大于 0';
                    continue;
                }
                if ($origin > 0 && $origin < $price) {
                    $errors[] = '「' . $title . '」原价不能低于折扣价';
                    continue;
                }

                $model->title = $title;
                $model->days = $days;
                $model->price = number_format($price, 2, '.', '');
                $model->origin_price = $origin > 0 ? number_format($origin, 2, '.', '') : '0.00';
                $model->badge = trim((string)($p['badge'] ?? ''));
                $model->is_active = (int)($p['is_active'] ?? 0) === 1 ? 1 : 0;
                $model->sort = (int)($p['sort'] ?? $model->sort);
                $model->updated_at = time();
                if ($model->save(false)) {
                    $saved++;
                }
            }
            $tx->commit();
        } catch (\Throwable $e) {
            if ($tx->isActive) {
                $tx->rollBack();
            }
            Yii::error('vip plan save failed: ' . $e->getMessage(), 'admin');
            Yii::$app->session->setFlash('error', '保存失败：' . $e->getMessage());
            return $this->redirect(['index', 'scope' => $scope]);
        }

        if ($errors) {
            Yii::$app->session->setFlash('error', implode('；', $errors));
        }
        if ($saved > 0) {
            Yii::$app->session->setFlash('success', '已保存 ' . $saved . ' 个' . VipPlan::scopeName($scope) . '档位');
        }
        return $this->redirect(['index', 'scope' => $scope]);
    }

    /**
     * 渠道专属价：保存一组渠道号的三档价（同一渠道再保存 = 覆盖）
     */
    public function actionChannelSave()
    {
        if (!Yii::$app->request->isPost) {
            return $this->redirect(['index']);
        }
        $post = Yii::$app->request->post();
        $scope = VipPlan::normalizeScope(isset($post['scope']) ? $post['scope'] : VipPlan::SCOPE_NOVEL);
        $codes = VipPlanChannel::splitCodes(isset($post['codes']) ? $post['codes'] : '');
        $prices = (array)(isset($post['price']) ? $post['price'] : []);
        $origins = (array)(isset($post['origin_price']) ? $post['origin_price'] : []);
        $badges = (array)(isset($post['badge']) ? $post['badge'] : []);

        if (!$codes) {
            Yii::$app->session->setFlash('error', '请至少填一个渠道号');
            return $this->redirect(['index', 'scope' => $scope]);
        }

        $bad = [];
        foreach ($codes as $c) {
            if (!preg_match('/^[A-Za-z0-9_\-]+$/', $c)) {
                $bad[] = $c;
            }
        }
        if ($bad) {
            Yii::$app->session->setFlash('error', '渠道号只能包含字母 / 数字 / 下划线 / 横线，这几个不对：' . implode('、', $bad));
            return $this->redirect(['index', 'scope' => $scope]);
        }

        $missing = [];
        $badPrice = [];
        $plans = VipPlan::allPlans($scope);
        foreach ($plans as $p) {
            $pid = (int)$p->id;
            $price = isset($prices[$pid]) ? (float)$prices[$pid] : 0;
            $origin = isset($origins[$pid]) ? (float)$origins[$pid] : 0;
            if ($price <= 0) {
                $missing[] = $p->title;
            } elseif ($origin > 0 && $origin < $price) {
                $badPrice[] = $p->title;
            }
        }
        if ($missing) {
            Yii::$app->session->setFlash('error', '这些档位的渠道价没填：' . implode('、', $missing));
            return $this->redirect(['index', 'scope' => $scope]);
        }
        if ($badPrice) {
            Yii::$app->session->setFlash('error', '划线价不能低于售价：' . implode('、', $badPrice));
            return $this->redirect(['index', 'scope' => $scope]);
        }

        $n = VipPlanChannel::saveSet($scope, $codes, $prices, $origins, $badges);
        Yii::$app->session->setFlash('success', '已保存 ' . count($codes) . ' 个渠道号（' . implode('、', $codes) . '）的专属价');
        return $this->redirect(['index', 'scope' => $scope]);
    }

    /**
     * 删除某个渠道的专属价（恢复默认价）
     */
    public function actionChannelDelete()
    {
        if (!Yii::$app->request->isPost) {
            return $this->redirect(['index']);
        }
        $post = Yii::$app->request->post();
        $scope = VipPlan::normalizeScope(isset($post['scope']) ? $post['scope'] : VipPlan::SCOPE_NOVEL);
        $code = trim((string)(isset($post['code']) ? $post['code'] : ''));
        $n = VipPlanChannel::removeCode($scope, $code);
        if ($n > 0) {
            Yii::$app->session->setFlash('success', '已删除渠道「' . $code . '」的专属价，该渠道恢复默认价');
        } else {
            Yii::$app->session->setFlash('error', '没有找到渠道「' . $code . '」的专属价');
        }
        return $this->redirect(['index', 'scope' => $scope]);
    }

    /**
     * 一键把「小说VIP套餐」的天数 / 原价 / 折扣价 / 角标复制到「浏览器VIP套餐」
     *
     * 用户要求两套价格一致，改小说价之后点一下这里就同步，不用手抄一遍。
     */
    public function actionSync()
    {
        if (!Yii::$app->request->isPost) {
            return $this->redirect(['index', 'scope' => VipPlan::SCOPE_BROWSER]);
        }
        $scope = VipPlan::normalizeScope(Yii::$app->request->post('scope', VipPlan::SCOPE_BROWSER));
        if ($scope !== VipPlan::SCOPE_BROWSER) {
            Yii::$app->session->setFlash('error', '只能把小说套餐同步到浏览器套餐');
            return $this->redirect(['index', 'scope' => $scope]);
        }

        $srcs = VipPlan::allPlans(VipPlan::SCOPE_NOVEL);
        $count = 0;
        foreach ($srcs as $src) {
            $dst = VipPlan::byCode($src->code, VipPlan::SCOPE_BROWSER);
            if (!$dst) {
                $dst = new VipPlan();
                $dst->scope = VipPlan::SCOPE_BROWSER;
                $dst->code = (string)$src->code;
                $dst->is_active = (int)$src->is_active;
                $dst->sort = (int)$src->sort;
            }
            $dst->title = $src->title;
            $dst->days = (int)$src->days;
            $dst->origin_price = $src->origin_price;
            $dst->price = $src->price;
            $dst->badge = $src->badge;
            $dst->updated_at = time();
            if ($dst->save(false)) {
                $count++;
            }
        }

        Yii::$app->session->setFlash('success', '已按小说套餐同步 ' . $count . ' 个档位（名称 / 天数 / 原价 / 折扣价 / 角标）');
        return $this->redirect(['index', 'scope' => VipPlan::SCOPE_BROWSER]);
    }

    /**
     * 米帧盒VIP套餐：新增档位。
     *
     * 只对 scope=mizhenhe 开放；小说 / 浏览器两套保持原有三档，不受影响。
     * 米帧盒播放器原生 App 与 H5 会员页都读这一套，保存后刷新即可生效。
     */
    public function actionCreate()
    {
        if (!Yii::$app->request->isPost) {
            return $this->redirect(['index', 'scope' => VipPlan::SCOPE_MIZHENHE]);
        }
        $post = Yii::$app->request->post();
        $scope = VipPlan::normalizeScope(isset($post['scope']) ? $post['scope'] : VipPlan::SCOPE_MIZHENHE);
        if ($scope !== VipPlan::SCOPE_MIZHENHE) {
            Yii::$app->session->setFlash('error', '新增档位只在「米帧盒VIP套餐」里开放，原两套套餐保持原样。');
            return $this->redirect(['index', 'scope' => $scope]);
        }

        $title = trim((string)(isset($post['title']) ? $post['title'] : ''));
        $days = (int)(isset($post['days']) ? $post['days'] : 0);
        $price = (float)(isset($post['price']) ? $post['price'] : 0);
        $origin = (float)(isset($post['origin_price']) ? $post['origin_price'] : 0);
        $badge = trim((string)(isset($post['badge']) ? $post['badge'] : ''));
        $sort = (int)(isset($post['sort']) ? $post['sort'] : 0);
        $code = trim((string)(isset($post['code']) ? $post['code'] : ''));
        if ($code === '') {
            $code = 'mzh_' . date('YmdHis');
        }
        if (!preg_match('/^[A-Za-z0-9_\-]{1,20}$/', $code)) {
            Yii::$app->session->setFlash('error', '档位标识只能用 20 位以内的字母 / 数字 / 下划线 / 横线');
            return $this->redirect(['index', 'scope' => $scope]);
        }
        if ($title === '' || $days < 1 || $days > 36500 || $price <= 0) {
            Yii::$app->session->setFlash('error', '请填档位名称、开通天数（1~36500）和大于 0 的折扣价');
            return $this->redirect(['index', 'scope' => $scope]);
        }
        if ($origin > 0 && $origin < $price) {
            Yii::$app->session->setFlash('error', '原价不能低于折扣价');
            return $this->redirect(['index', 'scope' => $scope]);
        }
        if (VipPlan::byCode($code, VipPlan::SCOPE_MIZHENHE)) {
            Yii::$app->session->setFlash('error', '档位标识「' . $code . '」已存在，换一个再试');
            return $this->redirect(['index', 'scope' => $scope]);
        }

        $m = new VipPlan();
        $m->scope = VipPlan::SCOPE_MIZHENHE;
        $m->code = $code;
        $m->title = $title;
        $m->days = $days;
        $m->price = number_format($price, 2, '.', '');
        $m->origin_price = $origin > 0 ? number_format($origin, 2, '.', '') : '0.00';
        $m->badge = $badge;
        $m->is_active = (int)(isset($post['is_active']) ? $post['is_active'] : 1) === 1 ? 1 : 0;
        $m->sort = $sort;
        $m->updated_at = time();
        $m->save(false);
        Yii::$app->session->setFlash('success', '已新增米帧盒档位「' . $title . '」（ID ' . $m->id . '），App 刷新 /v1/config 即生效');
        return $this->redirect(['index', 'scope' => VipPlan::SCOPE_MIZHENHE]);
    }

    /**
     * 米帧盒VIP套餐：删除档位。已有订单的档位只下架，保留历史订单对账数据。
     */
    public function actionDelete()
    {
        if (!Yii::$app->request->isPost) {
            return $this->redirect(['index', 'scope' => VipPlan::SCOPE_MIZHENHE]);
        }
        $post = Yii::$app->request->post();
        $scope = VipPlan::normalizeScope(isset($post['scope']) ? $post['scope'] : VipPlan::SCOPE_MIZHENHE);
        if ($scope !== VipPlan::SCOPE_MIZHENHE) {
            Yii::$app->session->setFlash('error', '删除档位只在「米帧盒VIP套餐」里开放。');
            return $this->redirect(['index', 'scope' => $scope]);
        }
        $m = VipPlan::find()->where([
            'id' => (int)(isset($post['id']) ? $post['id'] : 0),
            'scope' => VipPlan::SCOPE_MIZHENHE,
        ])->one();
        if (!$m) {
            Yii::$app->session->setFlash('error', '档位不存在');
            return $this->redirect(['index', 'scope' => $scope]);
        }
        $used = \app\models\RechargeOrder::find()->where(['vip_plan_id' => (int)$m->id])->exists();
        if ($used) {
            $m->is_active = 0;
            $m->updated_at = time();
            $m->save(false);
            Yii::$app->session->setFlash('success', '档位「' . $m->title . '」已有订单，已下架（历史订单对账数据保留）');
        } else {
            $name = $m->title;
            $m->delete();
            Yii::$app->session->setFlash('success', '已删除米帧盒档位「' . $name . '」');
        }
        return $this->redirect(['index', 'scope' => VipPlan::SCOPE_MIZHENHE]);
    }
}
