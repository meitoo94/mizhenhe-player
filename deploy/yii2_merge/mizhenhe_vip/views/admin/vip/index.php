<?php
/* @var $this yii\web\View */
/* @var $plans app\models\VipPlan[] */
/* @var $scope string novel | browser | mizhenhe */
/* @var $scopeNames array */

use yii\helpers\Html;
use yii\helpers\Url;

$isBrowser = $scope === 'browser';
$isMzh = $scope === \app\models\VipPlan::SCOPE_MIZHENHE;
$scopeLabel = $scopeNames[$scope];
$this->title = $scopeLabel;

/** 切换三套套餐的链接 */
$tabStyle = 'display:inline-block;padding:8px 18px;border-radius:999px;font-size:14px;text-decoration:none;';
$tabOn = $tabStyle . 'background:#ff6b35;color:#fff;';
$tabOff = $tabStyle . 'background:#f2f0ed;color:#6b645c;';
?>
<h1><?= Html::encode($scopeLabel) ?></h1>

<div class="admin-toolbar" style="margin-bottom:12px">
    <a href="<?= Url::to(['index', 'scope' => 'novel']) ?>" style="<?= $scope === 'novel' ? $tabOn : $tabOff ?>">📖 小说VIP套餐</a>
    <a href="<?= Url::to(['index', 'scope' => 'browser']) ?>" style="margin-left:8px;<?= $isBrowser ? $tabOn : $tabOff ?>">🛡️ 浏览器VIP套餐</a>
    <a href="<?= Url::to(['index', 'scope' => 'mizhenhe']) ?>" style="margin-left:8px;<?= $isMzh ? $tabOn : $tabOff ?>">📦 米帧盒VIP套餐</a>
</div>

<div class="admin-toolbar">
    <span class="hint">
        <?php if ($isMzh): ?>
            <b>米帧盒VIP套餐</b>：<b>米帧盒播放器原生 App 与 H5 演示的会员页，都只读这一套</b>。
            改完点「保存全部」，App 下次请求 <code>/v1/config</code> 就生效，<b>不用改「浏览器VIP套餐」在售档位、不用重新出包</b>；
            原生 App 与 H5 共用这里的档位，渠道展示后台「④ 套餐展示与价格」已停用。<br>
            <b>折扣价就是支付宝实付金额</b>，原价只用于划线展示；上架 / 下架决定 App 里显不显示；排序决定展示顺序。
            核对入口：<a href="https://app.kqi.ink/mzh/verify" target="_blank" style="color:#ff6b35">渠道配置验证台</a>（能看到两端实际拿到的档位与 plan_id）。
        <?php elseif ($isBrowser): ?>
            <b>浏览器VIP套餐</b>：App 在「浏览器形态」下，<b>浏览首页的每个功能（私密相册 / 独享视频 / 文件隐藏 / 音频隐藏 / 密码锁 / 密码生成器 / 更改图标）都要开通这一套才能使用</b>。
            浏览首页的会员条、浏览我的页的会员卡、功能拦截弹层，全部指向这一套。
            默认价格与小说套餐一致，改小说价之后可以点下面的「按小说套餐同步价格」一键复制。
        <?php else: ?>
            <b>小说VIP套餐</b>：书城「我的」页会员卡片、书币不足弹层的月卡/季卡、注册后的 VIP 封面、阅读页付费章节免币，都读这一套。
            浏览器形态的 App 不读这套（那套在「浏览器VIP套餐」里）。
        <?php endif; ?>
        <br>
        <b>折扣价</b>是用户实付金额，<b>原价</b>会在前台划掉展示（留 0 = 不显示划线价）。
        天数决定这一档开通多少天，续费从当前到期时间往后顺延。三套会员时长互不影响。
    </span>
</div>

<form method="post" action="<?= Url::to(['save']) ?>">
    <input type="hidden" name="<?= Yii::$app->request->csrfParam ?>" value="<?= Yii::$app->request->getCsrfToken() ?>">
    <input type="hidden" name="scope" value="<?= Html::encode($scope) ?>">

    <table class="atable">
        <tr>
            <th>ID</th>
            <th>档位标识</th>
            <th>档位名称</th>
            <th>开通天数</th>
            <th>原价（元）</th>
            <th>折扣价（元）</th>
            <th>折合每天</th>
            <th>角标文案</th>
            <th>排序</th>
            <th>上架</th>
            <?php if ($isMzh): ?><th>操作</th><?php endif; ?>
        </tr>
        <?php if (!$plans): ?>
            <tr><td colspan="<?= $isMzh ? 11 : 10 ?>" class="muted">这套套餐还没有档位，用下面的「新增米帧盒档位」加一档。</td></tr>
        <?php endif; ?>
        <?php foreach ($plans as $p): ?>
            <?php $id = (int)$p->id; ?>
            <tr>
                <td><?= $id ?></td>
                <td><code class="mono"><?= Html::encode($p->code) ?></code></td>
                <td>
                    <input type="text" name="plan[<?= $id ?>][title]" value="<?= Html::encode($p->title) ?>" style="width:110px">
                </td>
                <td>
                    <input type="number" name="plan[<?= $id ?>][days]" value="<?= (int)$p->days ?>" min="1" max="36500" style="width:90px">
                </td>
                <td>
                    <input type="number" step="0.01" min="0" name="plan[<?= $id ?>][origin_price]"
                           value="<?= Html::encode($p->origin_price) ?>" style="width:100px">
                </td>
                <td>
                    <input type="number" step="0.01" min="0.01" name="plan[<?= $id ?>][price]"
                           value="<?= Html::encode($p->price) ?>" style="width:100px">
                </td>
                <td>
                    ¥<?= $p->perDayText() ?> / 天
                    <?php if ($p->savingText() !== ''): ?>
                        <div class="small" style="color:#e2431f"><?= Html::encode($p->savingText()) ?></div>
                    <?php endif; ?>
                </td>
                <td>
                    <input type="text" name="plan[<?= $id ?>][badge]" value="<?= Html::encode($p->badge) ?>" placeholder="如 超值" style="width:90px">
                </td>
                <td>
                    <input type="number" name="plan[<?= $id ?>][sort]" value="<?= (int)$p->sort ?>" style="width:70px">
                </td>
                <td>
                    <label class="cd-check" style="display:inline-flex;align-items:center;gap:6px">
                        <input type="checkbox" name="plan[<?= $id ?>][is_active]" value="1" <?= (int)$p->is_active === 1 ? 'checked' : '' ?>>
                        <span><?= (int)$p->is_active === 1 ? '上架中' : '已下架' ?></span>
                    </label>
                </td>
                <?php if ($isMzh): ?>
                <td style="white-space:nowrap">
                    <form method="post" action="<?= Url::to(['delete']) ?>" style="display:inline"
                          onsubmit="return confirm('删除米帧盒档位「<?= Html::encode($p->title) ?>」？已有订单时会自动改为下架。')">
                        <input type="hidden" name="<?= Yii::$app->request->csrfParam ?>" value="<?= Yii::$app->request->getCsrfToken() ?>">
                        <input type="hidden" name="scope" value="mizhenhe">
                        <input type="hidden" name="id" value="<?= $id ?>">
                        <button type="submit" class="abtn">删除</button>
                    </form>
                </td>
                <?php endif; ?>
            </tr>
        <?php endforeach; ?>
    </table>

    <div class="admin-toolbar" style="margin-top:16px">
        <button type="submit" class="abtn">保存全部</button>
        <span class="hint" style="margin-left:12px">
            下架的档位前台不展示；已购买过该档的用户，会员时长不受影响。
        </span>
    </div>
</form>

<?php if ($isMzh): ?>
<div class="panel" style="margin-top:18px">
    <div class="panel-head"><h2>新增米帧盒档位</h2><span class="panel-sub">只写入米帧盒VIP套餐，不动小说 / 浏览器套餐</span></div>
    <form method="post" action="<?= Url::to(['create']) ?>" style="padding:14px 16px">
        <input type="hidden" name="<?= Yii::$app->request->csrfParam ?>" value="<?= Yii::$app->request->getCsrfToken() ?>">
        <input type="hidden" name="scope" value="mizhenhe">
        <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end">
            <label class="small">档位名称<br><input type="text" name="title" required placeholder="如 3年卡" style="width:120px"></label>
            <label class="small">档位标识<br><input type="text" name="code" maxlength="20" placeholder="留空自动生成" style="width:140px"></label>
            <label class="small">开通天数<br><input type="number" name="days" required min="1" max="36500" value="365" style="width:100px"></label>
            <label class="small">折扣价（元）<br><input type="number" name="price" required step="0.01" min="0.01" placeholder="98" style="width:100px"></label>
            <label class="small">原价（元）<br><input type="number" name="origin_price" step="0.01" min="0" placeholder="198" style="width:100px"></label>
            <label class="small">角标<br><input type="text" name="badge" maxlength="40" placeholder="如 超值" style="width:100px"></label>
            <label class="small">排序<br><input type="number" name="sort" value="100" style="width:80px"></label>
            <button type="submit" class="abtn">新增档位</button>
        </div>
        <div class="hint" style="margin-top:10px">新增后立即上架，App 刷新配置即可看到；折扣价需大于 0，原价留空 / 填 0 表示不显示划线价。</div>
    </form>
</div>
<?php endif; ?>

<?php if ($isBrowser): ?>
<div class="panel" style="margin-top:18px">
    <div class="panel-head"><h2>与小说套餐保持一致</h2><span class="panel-sub">价格 / 天数 / 角标</span></div>
    <form method="post" action="<?= Url::to(['sync']) ?>" style="padding:14px 16px">
        <input type="hidden" name="<?= Yii::$app->request->csrfParam ?>" value="<?= Yii::$app->request->getCsrfToken() ?>">
        <input type="hidden" name="scope" value="browser">
        <button type="submit" class="abtn" onclick="return confirm('把小说套餐的名称/天数/原价/折扣价/角标复制到浏览器套餐？浏览器套餐现有的价格会被覆盖。')">
            按小说套餐同步价格
        </button>
        <span class="hint" style="margin-left:12px">只在需要「两套价格一样」时点，同步后仍可单独改浏览器套餐。</span>
    </form>
</div>
<?php endif; ?>

<div class="panel" style="margin-top:20px">
    <div class="panel-head">
        <h2>渠道专属价</h2>
        <span class="panel-sub">默认全渠道走上面的价格，这里只配例外</span>
    </div>
    <div style="padding:14px 16px 0">
        <div class="hint" style="line-height:1.9">
            没配过的渠道号永远看上面的默认价。填了渠道号的用户（可一次填多个，换行 / 逗号 / 空格隔开）看这里的价。<br>
            <b>渠道号精确生效，互不影响</b>：填 <code class="mono">bbdd2</code> 只改 bbdd2 和它下面的二级 <code class="mono">bbdd2_1</code>；
            填 <code class="mono">bbdd</code> 只改 bbdd 自己（和它下面的二级），<b>不会</b>波及 bbdd1、bbdd16 这些并列的一级渠道。<br>
            要一次覆盖一整批，两种写法：<b>星号</b>（<code class="mono">bbdd*</code> = bbdd 开头的全部）或直接把多个号一起填（<code class="mono">bbdd1 bbdd2 bbdd3</code>）。<br>
            划线价留空（或填 0）= 沿用上面对应档位的原价。渠道号只认字母 / 数字 / 下划线 / 横线（星号只用于结尾通配）。
        </div>
        <form method="post" action="<?= Url::to(['channel-save']) ?>" id="vcForm" style="margin-top:12px">
            <input type="hidden" name="<?= Yii::$app->request->csrfParam ?>" value="<?= Yii::$app->request->getCsrfToken() ?>">
            <input type="hidden" name="scope" value="<?= Html::encode($scope) ?>">
            <table class="atable">
                <tr>
                    <th style="width:38%">渠道号</th>
                    <?php foreach ($plans as $p): ?>
                        <th><?= Html::encode($p->title) ?><div class="small muted" style="font-weight:400">售价 / 划线价 / 角标</div></th>
                    <?php endforeach; ?>
                </tr>
                <tr>
                    <td>
                        <textarea name="codes" id="vcCodes" rows="3" style="width:100%;box-sizing:border-box"
                                  placeholder="bbdd&#10;bbdd3&#10;多个用换行或逗号隔开"></textarea>
                    </td>
                    <?php foreach ($plans as $p): $pid = (int)$p->id; ?>
                        <td>
                            <input type="number" step="0.01" min="0.01" name="price[<?= $pid ?>]" id="vcPrice<?= $pid ?>"
                                   style="width:88px" placeholder="<?= Html::encode($p->price) ?>" title="售价">
                            <input type="number" step="0.01" min="0" name="origin_price[<?= $pid ?>]" id="vcOrigin<?= $pid ?>"
                                   style="width:88px;margin-top:6px" placeholder="<?= Html::encode($p->origin_price) ?>" title="划线价，留空沿用默认">
                            <input type="text" maxlength="32" name="badge[<?= $pid ?>]" id="vcBadge<?= $pid ?>"
                                   style="width:88px;margin-top:6px" placeholder="<?= Html::encode($p->badge) !== '' ? Html::encode($p->badge) : '不显示' ?>"
                                   title="角标：留空沿用默认档；填一个减号 - 表示这个渠道不显示角标">
                        </td>
                    <?php endforeach; ?>
                </tr>
            </table>
            <div class="admin-toolbar" style="margin-top:12px">
                <button type="submit" class="abtn">保存渠道价</button>
                <span class="hint" style="margin-left:12px">同一个渠道号再保存一次会覆盖原来的价。</span>
            </div>
        </form>
    </div>

    <?php if (!empty($channelRows)): ?>
        <div class="table-scroll" style="margin-top:6px">
            <table class="atable">
                <tr>
                    <th>渠道号</th>
                    <?php foreach ($plans as $p): ?>
                        <th><?= Html::encode($p->title) ?></th>
                    <?php endforeach; ?>
                    <th>更新时间</th>
                    <th>操作</th>
                </tr>
                <?php foreach ($channelRows as $rowCode => $row): ?>
                    <?php
                    $json = [];
                    foreach ($plans as $p) {
                        $pid = (int)$p->id;
                        $pr = isset($row['prices'][$pid]) ? $row['prices'][$pid] : null;
                        $json[$pid] = [
                            'p' => $pr ? (float)$pr->price : '',
                            'o' => $pr ? (float)$pr->origin_price : '',
                            'b' => $pr ? (string)$pr->badge : '',
                        ];
                    }
                    ?>
                    <tr>
                        <td>
                            <code class="mono"><?= Html::encode($rowCode) ?></code>
                            <?php if (substr($rowCode, -1) === '*'): ?>
                                <span class="muted small" style="margin-left:4px">通配 · 覆盖 <?= Html::encode(rtrim($rowCode, '*')) ?> 开头的渠道</span>
                            <?php else: ?>
                                <span class="muted small" style="margin-left:4px">仅此渠道<?= preg_match('/_/', $rowCode) ? '' : '（含其二级渠道）' ?></span>
                            <?php endif; ?>
                        </td>
                        <?php foreach ($plans as $p): $pid = (int)$p->id; $pr = isset($row['prices'][$pid]) ? $row['prices'][$pid] : null; ?>
                            <td>
                                <?php if ($pr): ?>
                                    <b>¥<?= Html::encode(rtrim(rtrim(number_format((float)$pr->price, 2, '.', ''), '0'), '.')) ?></b>
                                    <?php if ((float)$pr->origin_price > 0): ?>
                                        <span class="muted" style="text-decoration:line-through">¥<?= Html::encode(rtrim(rtrim(number_format((float)$pr->origin_price, 2, '.', ''), '0'), '.')) ?></span>
                                    <?php endif; ?>
                                    <?php $bd = trim((string)$pr->badge); ?>
                                    <?php if ($bd === '-'): ?>
                                        <div class="small muted">角标：不显示</div>
                                    <?php elseif ($bd !== ''): ?>
                                        <div class="small" style="color:#e2431f">角标：<?= Html::encode($bd) ?></div>
                                    <?php endif; ?>
                                <?php else: ?>
                                    <span class="muted">—</span>
                                <?php endif; ?>
                            </td>
                        <?php endforeach; ?>
                        <td class="muted"><?= $row['updated_at'] ? date('Y-m-d H:i', (int)$row['updated_at']) : '—' ?></td>
                        <td style="white-space:nowrap">
                            <button type="button" class="abtn" onclick="vcLoad(this)"
                                    data-code="<?= Html::encode($rowCode) ?>"
                                    data-json="<?= Html::encode(json_encode($json)) ?>">载入修改</button>
                            <form method="post" action="<?= Url::to(['channel-delete']) ?>" style="display:inline"
                                  onsubmit="return confirm('删除渠道「<?= Html::encode($rowCode) ?>」的专属价？该渠道会恢复默认价。')">
                                <input type="hidden" name="<?= Yii::$app->request->csrfParam ?>" value="<?= Yii::$app->request->getCsrfToken() ?>">
                                <input type="hidden" name="scope" value="<?= Html::encode($scope) ?>">
                                <input type="hidden" name="code" value="<?= Html::encode($rowCode) ?>">
                                <button type="submit" class="abtn">删除</button>
                            </form>
                        </td>
                    </tr>
                <?php endforeach; ?>
            </table>
        </div>
    <?php else: ?>
        <div style="padding:8px 16px 16px" class="muted">还没有渠道专属价，当前所有渠道都在用上面的默认价。</div>
    <?php endif; ?>
</div>

<script>
function vcLoad(btn) {
    var code = btn.getAttribute('data-code') || '';
    var data = {};
    try { data = JSON.parse(btn.getAttribute('data-json') || '{}'); } catch (e) { data = {}; }
    var box = document.getElementById('vcCodes');
    box.value = code;
    for (var k in data) {
        if (!data.hasOwnProperty(k)) continue;
        var a = document.getElementById('vcPrice' + k);
        var b = document.getElementById('vcOrigin' + k);
        var c = document.getElementById('vcBadge' + k);
        if (a) a.value = data[k].p;
        if (b) b.value = data[k].o;
        if (c) c.value = data[k].b || '';
    }
    box.scrollIntoView({behavior: 'smooth', block: 'center'});
    box.focus();
}
</script>

<div class="panel" style="margin-top:20px">
    <div class="panel-head"><h2>前台展示效果</h2><span class="panel-sub">按「排序」从小到大</span></div>
    <div class="table-scroll">
        <table class="atable">
            <tr><th>档位</th><th>价格</th><th>原价</th><th>折合每天</th><th>角标</th><th>状态</th></tr>
            <?php foreach ($plans as $p): ?>
                <tr>
                    <td><b><?= Html::encode($p->title) ?></b> <span class="muted">（<?= (int)$p->days ?> 天）</span></td>
                    <td class="money">¥<?= $p->priceText() ?></td>
                    <td class="muted"><?= (float)$p->origin_price > 0 ? '¥' . $p->originPriceText() : '—' ?></td>
                    <td>¥<?= $p->perDayText() ?></td>
                    <td><?= $p->badge !== '' ? Html::encode($p->badge) : '—' ?></td>
                    <td><?= (int)$p->is_active === 1 ? '<span style="color:#1a9e54">展示中</span>' : '<span style="color:#999">已下架</span>' ?></td>
                </tr>
            <?php endforeach; ?>
        </table>
    </div>
</div>
