# RLWE 分布式三层 HFL 聚合方案详细推导

## 0. 架构结论

物理架构严格保持为三层：

$ 
\boxed{\text{å®¢æ·ç«¯å±}\longrightarrow \text{è¾¹ç¼å±}\longrightarrow \text{äºå±}}
 $

边缘层内部包含两类功能节点：



1. **UAV / 接入边缘（数据平面）**：接收客户端密文、认证客户端、执行密文加法聚合，不持有长期私钥。

2. **地面阈值 PRE 委员会（密钥 / 控制平面）**：以 $t$-of-$M$ 方式持有密钥切换材料，把边缘聚合密文转换到云公钥下，不接触单个客户端密文。

因此，UAV 与地面 PRE 委员会之间是边缘层内部的东西向交互，不是独立第四层。

本方案的核心闭环为：

$ 
ct_i=\operatorname{Enc}_{pk_E}(n_i m_i),
 $

$ 
CT_e^{(E)}=\sum_{i\in S_e}ct_i,
 $

$ 
CT_e^{(C)}=\operatorname{ThresholdKeySwitch}_{E\to C}(CT_e^{(E)}),
 $

$ 
M=\operatorname{Dec}_{sk_C}\left(\sum_{e=1}^{L}CT_e^{(C)}\right) =\sum_{e=1}^{L}\sum_{i\in S_e}n_i m_i,
 $

$ 
\boxed{\bar m=\frac{M}{N},\qquad N=\sum_{e=1}^{L}\sum_{i\in S_e}n_i.}
 $

客户端和 UAV 均不上传传统部分解密份额。



***

## 1. 符号系统

### 1.1 环与分布

设 RLWE 环为：

$ 
R=\mathbb Z[X]/(X^d+1),
 $

$ 
R_q=R/qR,
 $

其中：



* $d$ 是环维度，例如 $d=2^{14}$；

* $q$ 是密文模；

* $\Delta$ 是明文缩放因子。

若使用 BFV：

$ 
\Delta=\left\lfloor\frac{q}{t_{\rm pt}}\right\rfloor,
 $

其中 $t_{\rm pt}$ 是明文模。

若使用 CKKS：

$ 
\Delta
 $

是固定缩放尺度，最终通过缩放解码得到实数向量。

定义以下分布：



* $\chi_k$：私钥分布，通常采样系数属于 $\{-1,0,1\}$；

* $\chi_e$：RLWE 小噪声分布，通常为离散高斯；

* $\chi_u$：加密随机量分布，可采用小系数分布或均匀分布。

记号：

$ 
x\leftarrow\chi
 $

表示从分布 $\chi$ 中采样。

### 1.2 实体编号



* 客户端：

$ 
i=1,\dots,K;
 $



* UAV / 接入边缘：

$ 
e=1,\dots,L;
 $



* 地面 PRE 委员会节点：

$ 
u=1,\dots,M;
 $



* PRE 阈值：任意不少于 $t$ 个委员会节点可以执行密钥切换；

* UAV $e$ 最终接收的客户端集合：

$ 
S_e;
 $



* 第 $r$ 轮全局最终参与集合：

$ 
S=\bigcup_{e=1}^{L}S_e;
 $



* UAV $e$ 下客户端数量：

$ 
K_e=|S_e|;
 $



* 当轮在线客户端总数：

$ 
K_r=\sum_{e=1}^{L}K_e.
 $

### 1.3 样本数与模型量



* 客户端 $i$ 的本地训练样本数：

$ 
n_i;
 $



* 客户端 $i$ 的本地模型更新编码到环上：

$ 
m_i\in R;
 $



* UAV $e$ 覆盖的样本数：

$ 
N_e=\sum_{i\in S_e}n_i;
 $



* 全局样本数：

$ 
N=\sum_{e=1}^{L}N_e =\sum_{e=1}^{L}\sum_{i\in S_e}n_i.
 $

正确的层次 FedAvg 结果为：

$ 
\boxed{
\bar m=
\frac{1}{N}
\sum_{e=1}^{L}\sum_{i\in S_e}n_i m_i.
}
 $

### 1.4 数字分解参数

密钥切换使用基数 $\beta$，例如 $\beta=2$ 或 $\beta=2^r$。定义：

$ 
\ell=\left\lceil\log_\beta q\right\rceil.
 $

任意 $z\in R_q$ 可按系数做数字分解：

$ 
z=\sum_{j=0}^{\ell-1}d_j(z)\beta^j,
 $

其中每个数字多项式 $d_j(z)$ 的系数满足：

$ 
d_j(z)\in\left[-\frac{\beta}{2},\frac{\beta}{2}\right).
 $



***

## 2. 信任边界

### 2.1 UAV / 接入边缘

UAV 可能物理暴露，因此要求：



* 不持有边缘层私钥 $s_E$；

* 不持有云端私钥 $s_C$；

* 不持有完整重加密密钥；

* 只能做认证、转发和密文加法；

* 只能看到本区域客户端密文。

### 2.2 地面 PRE 委员会

地面 PRE 委员会节点供电和物理防护相对稳定：



* 持有密钥切换材料的阈值份额；

* 只接收 UAV 聚合后的密文；

* 不接收单个客户端密文；

* 任意 $t$ 个节点可完成密钥切换；

* 少于 $t$ 个节点不能完成密钥切换。

### 2.3 云端

云端：



* 持有云私钥 $s_C$；

* 只接收边缘层输出的云公钥密文；

* 不接收边缘层密钥；

* 不接收单个客户端密文；

* 只能解密全局聚合结果。

### 2.4 最小匿名集合

若某一轮某个 UAV 下只有一个诚实客户端，则该区域聚合本质上等价于个体模型。协议应要求：

$ 
N_e\ge N_{\min},
 $

或者将小区域合并后再进入密钥切换阶段。



***

## 3. 离线 Setup：边缘层阈值密钥生成

### 3.1 分布式私钥生成

每个地面 PRE 节点 $u$ 选择一个根秘密：

$ 
x_u\leftarrow\chi_k.
 $

然后选择次数为 $t-1$ 的多项式：

$ 
f_u(X) =
x_u+\sum_{\ell=1}^{t-1}a_{u,\ell}X^\ell,
\qquad a_{u,\ell}\leftarrow\chi_k.
 $

选择公开且互不相同的插值点：

$ 
\alpha_1,\dots,\alpha_M.
 $

节点 $u$ 通过认证加密信道把份额：

$ 
\rho_{u,v}=f_u(\alpha_v)
 $

发送给节点 $v$。

当 $u\ne v$ 时，每个节点发送 $M-1$ 条私有份额消息；全网总数为：

$ 
\boxed{M(M-1)\text{ æ¡ç§æ Setup æ¶æ¯}}
 $

每条消息包含一个 $R_q$ 元素。

节点 $v$ 收到所有份额后计算：

$ 
s_v=\sum_{u=1}^{M}\rho_{u,v} =\sum_{u=1}^{M}f_u(\alpha_v).
 $

定义聚合多项式：

$ 
F(X)=\sum_{u=1}^{M}f_u(X).
 $

于是：

$ 
s_v=F(\alpha_v).
 $

边缘层全局私钥为：

$ 
s_E=F(0).
 $

由于：

$ 
F(0)=\sum_{u=1}^{M}f_u(0)=\sum_{u=1}^{M}x_u,
 $

所以：

$ 
\boxed{s_E=\sum_{u=1}^{M}x_u.}
 $

任何单个节点都不知道完整 $s_E$。

### 3.2 任意 t 个节点与 $s_E$ 的关系

因为：

$ 
\deg F\le t-1,
 $

任意 t 个点即可插值。设 $T\subseteq\{1,\dots,M\}$，且：

$ 
|T|=t.
 $

Lagrange 系数为：

$ 
\lambda_v =
\prod_{w\in T,w\ne v}
\frac{-\alpha_w}{\alpha_v-\alpha_w}.
 $

于是：

$ 
\boxed{
s_E=F(0)=\sum_{v\in T}\lambda_vs_v.
}
 $

注意：正常协议不重构 $s_E$，该关系只用于说明阈值性质。

### 3.3 边缘层公钥构造

从公开信标获得：

$ 
a_E\leftarrow R_q.
 $

每个 PRE 节点 $u$ 采样：

$ 
e_{u,E}\leftarrow\chi_e,
 $

并发布：

$ 
b_u=-a_E x_u+e_{u,E}.
 $

所有 $b_u$ 求和：

$ 
b_E=\sum_{u=1}^{M}b_u.
 $

展开：

$ 
b_E =
\sum_{u=1}^{M}(-a_Ex_u+e_{u,E}) =
-a_E\sum_{u=1}^{M}x_u+\sum_{u=1}^{M}e_{u,E}.
 $

因此：

$ 
\boxed{
b_E=-a_Es_E+e_E,
}
 $

其中：

$ 
e_E=\sum_{u=1}^{M}e_{u,E}.
 $

边缘层公钥为：

$ 
\boxed{pk_E=(b_E,a_E).}
 $

该阶段：



* 每个节点发布一个 $R_q$ 元素 $b_u$；

* 共发布 $M$ 个 $R_q$ 元素；

* 最终公钥包含 2 个 $R_q$ 元素。



***

## 4. 离线 Setup：云端密钥生成

云端独立生成自己的私钥：

$ 
s_C\leftarrow\chi_k.
 $

选择：

$ 
a_C\leftarrow R_q,\qquad e_C\leftarrow\chi_e.
 $

计算：

$ 
b_C=-a_Cs_C+e_C.
 $

云端公钥为：

$ 
\boxed{pk_C=(b_C,a_C).}
 $

云端私钥 $s_C$ 不发给边缘节点和客户端。



***

## 5. 离线 Setup：E 到 C 的密钥切换材料

为了把边缘公钥 $pk_E$ 下的密文切换到云公钥 $pk_C$ 下，需要构造密钥切换密钥：

$ 
KSK_{E\to C}.
 $

其本质是：

$ 
\boxed{\text{å¨äºå¬é¥ }pk_C\text{ ä¸å å¯è¾¹ç¼ç§é¥ }s_E\text{ çåä¸ªæ°å­åéã}}
 $

### 5.1 每个 PRE 节点生成贡献

对每个节点 $u$ 和每个数字层 $j=0,\dots,\ell-1$，采样：

$ 
r_{u,j}\leftarrow\chi_u,
 $

$ 
\epsilon_{u,j,0}\leftarrow\chi_e,
\qquad
\epsilon_{u,j,1}\leftarrow\chi_e.
 $

计算：

$ 
k_{u,j,0} =
b_Cr_{u,j}+\beta^j x_u+\epsilon_{u,j,0},
 $

$ 
k_{u,j,1} =
a_Cr_{u,j}+\epsilon_{u,j,1}.
 $

节点 $u$ 的第 $j$ 个贡献为：

$ 
KSK_{u,j}=(k_{u,j,0},k_{u,j,1}).
 $

### 5.2 聚合所有节点贡献

定义：

$ 
k_{j,0}=\sum_{u=1}^{M}k_{u,j,0},
 $

$ 
k_{j,1}=\sum_{u=1}^{M}k_{u,j,1}.
 $

展开 $k_{j,0}$：

$ 
\begin{aligned}
k_{j,0}
&=
\sum_{u=1}^{M}
\left(
b_Cr_{u,j}+\beta^jx_u+\epsilon_{u,j,0}
\right)\\
&=
b_C\sum_{u=1}^{M}r_{u,j} +
\beta^j\sum_{u=1}^{M}x_u +
\sum_{u=1}^{M}\epsilon_{u,j,0}.
\end{aligned}
 $

定义：

$ 
r_j=\sum_{u=1}^{M}r_{u,j},
 $

$ 
\epsilon_{j,0}=\sum_{u=1}^{M}\epsilon_{u,j,0},
 $

并利用：

$ 
\sum_{u=1}^{M}x_u=s_E,
 $

得到：

$ 
\boxed{
k_{j,0}=b_Cr_j+\beta^js_E+\epsilon_{j,0}.
}
 $

展开 $k_{j,1}$：

$ 
\begin{aligned}
k_{j,1}
&=
\sum_{u=1}^{M}
\left(a_Cr_{u,j}+\epsilon_{u,j,1}\right)\\
&=
a_C\sum_{u=1}^{M}r_{u,j} +
\sum_{u=1}^{M}\epsilon_{u,j,1}.
\end{aligned}
 $

定义：

$ 
\epsilon_{j,1}=\sum_{u=1}^{M}\epsilon_{u,j,1},
 $

则：

$ 
\boxed{
k_{j,1}=a_Cr_j+\epsilon_{j,1}.
}
 $

因此，第 $j$ 个全局密钥切换材料为：

$ 
\boxed{
KSK_j=(k_{j,0},k_{j,1}) =
\operatorname{RLWE}_{pk_C}(\beta^js_E).
}
 $

### 5.3 验证 KSK 的 RLWE 结构

在云私钥 $s_C$ 下，有：

$ 
k_{j,0}+k_{j,1}s_C.
 $

代入：

$ 
k_{j,0}=b_Cr_j+\beta^js_E+\epsilon_{j,0},
 $

$ 
k_{j,1}=a_Cr_j+\epsilon_{j,1},
 $

以及：

$ 
b_C=-a_Cs_C+e_C,
 $

得到：

$ 
\begin{aligned}
k_{j,0}+k_{j,1}s_C
&=
(-a_Cs_C+e_C)r_j
+\beta^js_E
+\epsilon_{j,0}
+(a_Cr_j+\epsilon_{j,1})s_C\\
&=
-a_Cs_Cr_j+e_Cr_j
+\beta^js_E
+\epsilon_{j,0}
+a_Cr_js_C
+\epsilon_{j,1}s_C\\
&=
\beta^js_E
+e_Cr_j
+\epsilon_{j,0}
+\epsilon_{j,1}s_C.
\end{aligned}
 $

定义小噪声：

$ 
\nu_j =
e_Cr_j+\epsilon_{j,0}+\epsilon_{j,1}s_C,
 $

则：

$ 
\boxed{
k_{j,0}+k_{j,1}s_C=\beta^js_E+\nu_j.
}
 $

### 5.4 KSK 的阈值分发

全局 $KSK_j$ 不能被单个节点持有，也不能发给云。对每个 $j$ 和每个分量 $b\in\{0,1\}$，通过可验证秘密共享选择次数为 $t-1$ 的多项式：

$ 
\kappa_{j,b}(0)=k_{j,b},
\qquad \deg\kappa_{j,b}\le t-1.
 $

节点 $u$ 收到：

$ 
k_{j,b,u}=\kappa_{j,b}(\alpha_u).
 $

任意 t 个节点满足：

$ 
\boxed{
k_{j,b}=\sum_{u\in T}\lambda_u k_{j,b,u}.
}
 $

全局 KSK 包含：

$ 
2\ell
 $

个 $R_q$ 元素。若把每个元素阈值分发给 M 个节点，则产生：

$ 
\boxed{2\ell M\text{ ä¸ª }R_q\text{ ç­æä»½é¢}.}
 $

这些材料只在边缘层内部保存。



***

## 6. 每轮协议：客户端加密

### 6.1 轮次标签

定义第 $r$ 轮、UAV $e$ 的标签：

$ 
\tau_{r,e} =
H(r\|e\|pk_E\|pk_C).
 $

该标签用于防止跨轮、跨区域重放。

### 6.2 客户端加密公式

客户端 $i$ 采样：

$ 
u_i\leftarrow\chi_u,
 $

$ 
e_{i,0}\leftarrow\chi_e,
\qquad
e_{i,1}\leftarrow\chi_e.
 $

客户端需要加密的明文是样本数加权更新：

$ 
x_i=n_i m_i.
 $

计算：

$ 
\boxed{
c_{i,0} =
b_Eu_i+\Delta(n_i m_i)+e_{i,0},
}
 $

$ 
\boxed{
c_{i,1} =
a_Eu_i+e_{i,1}.
}
 $

客户端密文为：

$ 
ct_i=(c_{i,0},c_{i,1}).
 $

客户端对密文签名：

$ 
\sigma_i =
\operatorname{Sign}_{sk_i}
\left(
H(\tau_{r,e}\|n_i\|c_{i,0}\|c_{i,1})
\right).
 $

### 6.3 客户端发送的消息

客户端 $i$ 向其接入 UAV $e$ 发送：

$ 
\boxed{
M_{i\to e} =
\left(
r,e,i,n_i,\tau_{r,e},c_{i,0},c_{i,1},\sigma_i
\right).
}
 $

其中密码学密文负载为 2 个 $R_q$ 元素。

若当轮有 $K_r$ 个客户端上传，则客户端总上行密文通信量为：

$ 
\boxed{2K_r\text{ ä¸ª }R_q\text{ åç´ }.}
 $

客户端不发送部分解密份额。



***

## 7. UAV 边缘聚合

设 UAV $e$ 的最终有效客户端集合为：

$ 
S_e=\{i_{e,1},i_{e,2},\dots,i_{e,K_e}\}.
 $

UAV 首先验证每个客户端的签名：

$ 
\operatorname{Verify}
\left(
pk_i,\sigma_i,
H(\tau_{r,e}\|n_i\|c_{i,0}\|c_{i,1})
\right)=1.
 $

### 7.1 聚合第一分量

定义：

$ 
C_{e,0}=\sum_{i\in S_e}c_{i,0}.
 $

完整展开：

$ 
\begin{aligned}
C_{e,0}
&=
\sum_{i\in S_e}
\left[
b_Eu_i+\Delta(n_i m_i)+e_{i,0}
\right]\\
&=
b_E\sum_{i\in S_e}u_i +
\Delta\sum_{i\in S_e}n_i m_i +
\sum_{i\in S_e}e_{i,0}.
\end{aligned}
 $

定义：

$ 
u_e=\sum_{i\in S_e}u_i,
 $

$ 
M_e=\sum_{i\in S_e}n_i m_i,
 $

$ 
e_{e,0}=\sum_{i\in S_e}e_{i,0}.
 $

则：

$ 
\boxed{
C_{e,0}=b_Eu_e+\Delta M_e+e_{e,0}.
}
 $

### 7.2 聚合第二分量

定义：

$ 
C_{e,1}=\sum_{i\in S_e}c_{i,1}.
 $

完整展开：

$ 
\begin{aligned}
C_{e,1}
&=
\sum_{i\in S_e}(a_Eu_i+e_{i,1})\\
&=
a_E\sum_{i\in S_e}u_i +
\sum_{i\in S_e}e_{i,1}.
\end{aligned}
 $

定义：

$ 
e_{e,1}=\sum_{i\in S_e}e_{i,1},
 $

则：

$ 
\boxed{
C_{e,1}=a_Eu_e+e_{e,1}.
}
 $

UAV 的边缘聚合密文为：

$ 
\boxed{
CT_e^{(E)}=(C_{e,0},C_{e,1}).
}
 $

### 7.3 边缘聚合密文的解密结构

虽然 UAV 不执行解密，但可以验证其代数结构：

$ 
C_{e,0}+C_{e,1}s_E.
 $

代入：

$ 
C_{e,0}=b_Eu_e+\Delta M_e+e_{e,0},
 $

$ 
C_{e,1}=a_Eu_e+e_{e,1},
 $

$ 
b_E=-a_Es_E+e_E.
 $

得到：

$ 
\begin{aligned}
C_{e,0}+C_{e,1}s_E
&=
(-a_Es_E+e_E)u_e
+\Delta M_e
+e_{e,0}
+(a_Eu_e+e_{e,1})s_E\\
&=
-a_Es_Eu_e+e_Eu_e
+\Delta M_e
+e_{e,0}
+a_Eu_es_E
+e_{e,1}s_E\\
&=
\Delta M_e
+e_Eu_e
+e_{e,0}
+e_{e,1}s_E.
\end{aligned}
 $

定义边缘聚合噪声：

$ 
\delta_{e,E} =
e_Eu_e+e_{e,0}+e_{e,1}s_E.
 $

因此：

$ 
\boxed{
C_{e,0}+C_{e,1}s_E=\Delta M_e+\delta_{e,E}.
}
 $



***

## 8. 聚合绑定证明

### 8.1 半诚实模型

若 UAV 是半诚实的，UAV 只需要提交：

$ 
(r,e,S_e,N_e,CT_e^{(E)},\{\sigma_i\}_{i\in S_e}).
 $

PRE 委员会根据认证策略和边缘层网络隔离，确认该密文来自 UAV 聚合通道。

### 8.2 恶意模型

若 UAV 可能恶意，则必须证明 $CT_e^{(E)}$ 是本批次客户端密文之和，而不是单个客户端密文。

UAV 生成证明：

$ 
\pi_e =
\operatorname{Prove}
\left(
r,e,S_e,\{ct_i\}_{i\in S_e},\{\sigma_i\}_{i\in S_e},CT_e^{(E)}
\right).
 $

证明关系为：

$ 
\mathcal R_{\rm Agg}
\left(
\begin{array}{l}
r,e,S_e,\{ct_i\},\{\sigma_i\},CT_e^{(E)}
\end{array}
\right)=1
 $

当且仅当：



1. 每个 $\sigma_i$ 是客户端 $i$ 对 $ct_i$ 的有效签名；

2. 所有客户端属于轮次 $r$ 和区域 $e$；

3. 密文分量满足：

$ 
C_{e,0}=\sum_{i\in S_e}c_{i,0},
 $

$ 
C_{e,1}=\sum_{i\in S_e}c_{i,1};
 $



1. UAV 没有插入额外密文；

2. UAV 没有把单个客户端密文伪装成聚合密文。

PRE 委员会验证：

$ 
\operatorname{Verify}_{\rm Agg}(\pi_e)=1.
 $

工程上可以组合使用：



* 客户端签名；

* Merkle 批次根；

* 密文承诺；

* 求和关系的零知识证明；

* 区域和轮次标签绑定。



***

## 9. 阈值密钥切换：边缘密文转换为云密文

PRE 协调者属于边缘层，不是独立层级。它把：

$ 
CT_e^{(E)}=(C_{e,0},C_{e,1})
 $

广播给选中的 t 个 PRE 节点。

### 9.1 分解第二分量

对 $C_{e,1}$ 做基数 $\beta$ 的数字分解：

$ 
\boxed{
C_{e,1} =
\sum_{j=0}^{\ell-1}d_{e,j}\beta^j.
}
 $

### 9.2 每个 PRE 节点计算部分密钥切换结果

节点 $u\in T$ 持有：

$ 
k_{j,0,u},\qquad k_{j,1,u}.
 $

计算：

$ 
\boxed{
P_{u,e,0} =
\lambda_u C_{e,0} +
\sum_{j=0}^{\ell-1}
d_{e,j}\lambda_u k_{j,0,u},
}
 $

$ 
\boxed{
P_{u,e,1} =
\sum_{j=0}^{\ell-1}
d_{e,j}\lambda_u k_{j,1,u}.
}
 $

节点 $u$ 把：

$ 
P_{u,e}=(P_{u,e,0},P_{u,e,1})
 $

发给边缘层 PRE 协调者。

每个节点为每个 UAV 发送 2 个 $R_q$ 元素。若有 t 个节点、L 个 UAV，则部分密钥切换结果总数为：

$ 
\boxed{2tL\text{ ä¸ª }R_q\text{ åç´ }.}
 $

这些消息是边缘层内部消息。

### 9.3 协调者组合

协调者计算：

$ 
C'_{e,0} =
\sum_{u\in T}P_{u,e,0},
 $

$ 
C'_{e,1} =
\sum_{u\in T}P_{u,e,1}.
 $

先展开 $C'_{e,0}$：

$ 
\begin{aligned}
C'_{e,0}
&=
\sum_{u\in T}
\left[
\lambda_u C_{e,0} +
\sum_{j=0}^{\ell-1}
d_{e,j}\lambda_u k_{j,0,u}
\right]\\
&=
C_{e,0}\sum_{u\in T}\lambda_u +
\sum_{j=0}^{\ell-1}
d_{e,j}
\sum_{u\in T}\lambda_u k_{j,0,u}.
\end{aligned}
 $

Lagrange 系数满足：

$ 
\sum_{u\in T}\lambda_u=1,
 $

且：

$ 
\sum_{u\in T}\lambda_u k_{j,0,u}=k_{j,0}.
 $

所以：

$ 
\boxed{
C'_{e,0} =
C_{e,0} +
\sum_{j=0}^{\ell-1}d_{e,j}k_{j,0}.
}
 $

再展开 $C'_{e,1}$：

$ 
\begin{aligned}
C'_{e,1}
&=
\sum_{u\in T}
\sum_{j=0}^{\ell-1}
d_{e,j}\lambda_u k_{j,1,u}\\
&=
\sum_{j=0}^{\ell-1}
d_{e,j}
\sum_{u\in T}\lambda_u k_{j,1,u}\\
&=
\sum_{j=0}^{\ell-1}d_{e,j}k_{j,1}.
\end{aligned}
 $

因此：

$ 
\boxed{
C'_{e,1} =
\sum_{j=0}^{\ell-1}d_{e,j}k_{j,1}.
}
 $

输出云公钥密文：

$ 
\boxed{
CT_e^{(C)}=(C'_{e,0},C'_{e,1}).
}
 $

### 9.4 验证密钥切换正确性

计算云密钥下的解密量：

$ 
C'_{e,0}+C'_{e,1}s_C.
 $

代入：

$ 
C'_{e,0} =
C_{e,0}+\sum_j d_{e,j}k_{j,0},
 $

$ 
C'_{e,1} =
\sum_j d_{e,j}k_{j,1}.
 $

得到：

$ 
\begin{aligned}
C'_{e,0}+C'_{e,1}s_C
&=
C_{e,0} +
\sum_jd_{e,j}
(k_{j,0}+k_{j,1}s_C).
\end{aligned}
 $

由 KSK 的结构：

$ 
k_{j,0}+k_{j,1}s_C=\beta^js_E+\nu_j,
 $

所以：

$ 
\begin{aligned}
C'_{e,0}+C'_{e,1}s_C
&=
C_{e,0} +
\sum_jd_{e,j}\beta^js_E +
\sum_jd_{e,j}\nu_j.
\end{aligned}
 $

根据数字分解：

$ 
\sum_jd_{e,j}\beta^j=C_{e,1},
 $

故：

$ 
C'_{e,0}+C'_{e,1}s_C =
C_{e,0}+C_{e,1}s_E +
\sum_jd_{e,j}\nu_j.
 $

定义密钥切换新增噪声：

$ 
\nu_e=\sum_{j=0}^{\ell-1}d_{e,j}\nu_j.
 $

又因为：

$ 
C_{e,0}+C_{e,1}s_E=\Delta M_e+\delta_{e,E},
 $

所以：

$ 
\boxed{
C'_{e,0}+C'_{e,1}s_C =
\Delta M_e+\delta_{e,E}+\nu_e.
}
 $

即：

$ 
\boxed{
CT_e^{(C)}=\operatorname{Enc}_{pk_C}(M_e)
}
 $

在 RLWE 近似意义下成立。

噪声满足确定性上界：

$ 
\|\nu_e\|_\infty
\le
\sum_{j=0}^{\ell-1}
\|d_{e,j}\|_\infty\|\nu_j\|_\infty
\le
\frac{\beta}{2}
\sum_{j=0}^{\ell-1}\|\nu_j\|_\infty.
 $



***

## 10. 云端全局聚合与解密

### 10.1 云层密文求和

云端收到每个边缘区域的云公钥密文：

$ 
CT_e^{(C)}=(C'_{e,0},C'_{e,1}).
 $

计算：

$ 
C_0^{(C)}=\sum_{e=1}^{L}C'_{e,0},
 $

$ 
C_1^{(C)}=\sum_{e=1}^{L}C'_{e,1}.
 $

全局云密文为：

$ 
CT^{(C)}=(C_0^{(C)},C_1^{(C)}).
 $

### 10.2 展开解密式

云端计算：

$ 
C_0^{(C)}+C_1^{(C)}s_C =
\sum_{e=1}^{L}
(C'_{e,0}+C'_{e,1}s_C).
 $

由：

$ 
C'_{e,0}+C'_{e,1}s_C =
\Delta M_e+\delta_{e,E}+\nu_e,
 $

得到：

$ 
\begin{aligned}
C_0^{(C)}+C_1^{(C)}s_C
&=
\sum_{e=1}^{L}
\left(\Delta M_e+\delta_{e,E}+\nu_e\right)\\
&=
\Delta\sum_{e=1}^{L}M_e +
\sum_{e=1}^{L}\delta_{e,E} +
\sum_{e=1}^{L}\nu_e.
\end{aligned}
 $

定义：

$ 
M=\sum_{e=1}^{L}M_e =\sum_{e=1}^{L}\sum_{i\in S_e}n_i m_i,
 $

$ 
\delta =
\sum_{e=1}^{L}\delta_{e,E} +
\sum_{e=1}^{L}\nu_e.
 $

于是：

$ 
\boxed{
C_0^{(C)}+C_1^{(C)}s_C =
\Delta M+\delta.
}
 $

### 10.3 解密与归一化

云端执行：

$ 
\boxed{
M =
\operatorname{Round}
\left(
\frac{C_0^{(C)}+C_1^{(C)}s_C}{\Delta}
\right).
}
 $

然后计算最终样本数：

$ 
N =
\sum_{e=1}^{L}N_e =
\sum_{e=1}^{L}\sum_{i\in S_e}n_i.
 $

全局模型更新为：

$ 
\boxed{
\bar m=\frac{M}{N}.
}
 $

BFV 正确解密要求：

$ 
\|\delta\|_\infty<\frac{\Delta}{2}.
 $

CKKS 则要求最终噪声相对缩放尺度足够小，以保证解码精度。



***

## 11. 每轮消息清单

### 11.1 客户端发送



| 发送方     | 接收方     | 消息内容                                              | 数量          |
| ------- | ------- | ------------------------------------------------- | ----------- |
| 客户端 $i$ | UAV $e$ | $(r,e,i,n_i,\tau_{r,e},c_{i,0},c_{i,1},\sigma_i)$ | 每个在线客户端 1 条 |

其中：

$ 
c_{i,0},c_{i,1}\in R_q.
 $

每个客户端发送 2 个 $R_q$ 密文元素。

$K_r$ 个客户端总密文通信量：

$ 
\boxed{2K_r\text{ ä¸ª }R_q\text{ åç´ }.}
 $

### 11.2 UAV 发送



| 发送方     | 接收方           | 消息内容                                  | 数量         |
| ------- | ------------- | ------------------------------------- | ---------- |
| UAV $e$ | PRE 协调者 / 委员会 | $(r,e,S_e,N_e,C_{e,0},C_{e,1},\pi_e)$ | 每个 UAV 1 条 |

每个 UAV 发送 2 个 $R_q$ 聚合元素。

L 个 UAV 总计：

$ 
\boxed{2L\text{ ä¸ª }R_q\text{ åç´ }.}
 $

UAV 不发送解密份额。

### 11.3 PRE 节点内部发送



| 发送方        | 接收方     | 消息内容                    | 数量           |
| ---------- | ------- | ----------------------- | ------------ |
| PRE 节点 $u$ | PRE 协调者 | $(P_{u,e,0},P_{u,e,1})$ | 每个 $u,e$ 1 条 |

t 个节点服务 L 个 UAV 时：

$ 
\boxed{2tL\text{ ä¸ª }R_q\text{ åç´ }.}
 $

这些是密钥切换结果，不是部分解密份额。

### 11.4 PRE 协调者发送给云



| 发送方     | 接收方 | 消息内容                  | 数量         |
| ------- | --- | --------------------- | ---------- |
| PRE 协调者 | 云   | $(C'_{e,0},C'_{e,1})$ | 每个 UAV 1 条 |

L 个 UAV 总计：

$ 
\boxed{2L\text{ ä¸ª }R_q\text{ åç´ }.}
 $

### 11.5 Setup 通信量



| 阶段       | 消息                         | 总数量                    |
| -------- | -------------------------- | ---------------------- |
| 边缘 DKG   | $u\to v$ 的 $f_u(\alpha_v)$ | $M(M-1)$ 个 $R_q$ 元素    |
| 边缘公钥     | $b_u$                      | $M$ 个 $R_q$ 元素         |
| KSK 贡献   | 每个节点提交 $2\ell$ 个元素         | $2M\ell$ 个 $R_q$ 元素    |
| 全局 KSK   | 聚合后的密钥切换材料                 | $2\ell$ 个 $R_q$ 元素     |
| KSK 阈值分发 | 每个 KSK 元素分发给 M 个节点         | $2\ell M$ 个 $R_q$ 等效份额 |



***

## 12. 掉线时隙与处理方式

### 12.1 Setup 阶段掉线

若地面 PRE 节点在 DKG 完成前掉线：



* 该节点不能进入委员会；

* 重新执行委员会注册或使用备用节点；

* 已完成 DKG 后，单个委员会节点掉线不影响协议，只要在线节点数不少于 t。

### 12.2 客户端在加密前掉线

客户端没有上传密文，则其本地更新无法恢复。

最终集合中排除该客户端：

$ 
S_e\leftarrow S_e\setminus\{i\}.
 $

同时样本数不包含 $n_i$。

### 12.3 客户端密文部分上传

若只收到一个密文分量或签名验证失败，UAV 丢弃该客户端的全部数据：

$ 
ct_i\text{ ä¸å®æ´}\Longrightarrow i\notin S_e.
 $

### 12.4 客户端上传密文后掉线

本方案中这是允许的。

客户端不需要再上传部分解密份额，因此不存在 “密文已到但解密份额未到” 的原子性缺口。

### 12.5 UAV 聚合后掉线

如果 UAV 在把：

$ 
CT_e^{(E)}
 $

发给地面 PRE 委员会之前掉线，且没有备份，则该区域数据无法恢复。

解决方法是在边缘层引入擦除编码中继：

$ 
CT_e^{(E)}
\mapsto
(E_1,\dots,E_h),
 $

并将编码块发送给多个地面边缘节点或备用 UAV。任意 $k$ 个编码块即可恢复：

$ 
CT_e^{(E)}=\operatorname{Decode}(E_{j_1},\dots,E_{j_k}).
 $

该过程仍属于边缘层内部。

### 12.6 PRE 节点掉线

密钥切换是 t-of-M：

$ 
\#\text{å¨çº¿ PRE èç¹}\ge t
 $

即可恢复。

若在线数少于 t，则无法完成密钥切换。

### 12.7 客户端跨 UAV 切换

车辆从 UAV $e$ 切换到 UAV $e'$ 时，以最终签名和会话票据确定归属。



* 若完整密文被 $e$ 接收，则 $i\in S_e$；

* 若在 $e'$ 完成上传，则 $i\in S_{e'}$；

* 不允许同一轮重复计入；

* 样本数跟随最终归属集合。



***

## 13. 隐私分析

### 13.1 UAV 视角

UAV 看到：

$ 
\{ct_i\}_{i\in S_e},
 $

但没有：

$ 
s_E,\quad s_C,\quad KSK.
 $

因此 UAV 不能解密个体更新，也不能把密文切换到云密钥下。

### 13.2 PRE 委员会视角

PRE 委员会只看到：

$ 
CT_e^{(E)},
 $

即区域聚合密文，不看到单个客户端密文。

少于 t 个节点不能恢复 KSK 或执行密钥切换。

### 13.3 云视角

云只收到：

$ 
\{CT_e^{(C)}\}_{e=1}^{L},
 $

并最终解密：

$ 
M=\sum_e\sum_{i\in S_e}n_i m_i.
 $

云不能获得单个客户端的边缘密文，也不能获得边缘私钥 $s_E$。

### 13.4 合谋边界

需要保证：

$ 
t>\kappa,
 $

其中 $\kappa$ 是攻击者可腐化的 PRE 委员会节点数量上限。

若 UAV 与少于 t 个 PRE 节点合谋：



* UAV 提供个体密文；

* 少量 PRE 节点无法独立完成密钥切换。

若云与少于 t 个 PRE 节点合谋：



* 云不能获得完整 KSK；

* 不能恢复边缘私钥 $s_E$。



***

## 14. 方案的创新性边界

单独使用 RLWE、Shamir、DKG 或密钥切换不构成创新。可形成论文贡献的点包括：

### 14.1 Aggregate-bound Threshold PRE

将密钥切换能力限制为只能作用于边缘聚合密文：

$ 
\boxed{
\text{åªæéè¿èåå³ç³»è¯æç }CT_e^{(E)}\text{ æè½è¢«åæ¢å°äºå¯é¥ä¸ã}
}
 $

### 14.2 UAV 零密钥的数据 / 密钥平面解耦

UAV 有个体密文但无密钥；地面 PRE 委员会有密钥份额但只看到聚合密文。

### 14.3 异构覆盖下的无偏加权聚合

边缘层不做局部均匀平均，只传递：

$ 
CT_e^{(E)},\qquad N_e,
 $

云端统一计算：

$ 
\bar m =
\frac{\sum_e\sum_{i\in S_e}n_i m_i}
{\sum_e\sum_{i\in S_e}n_i}.
 $

### 14.4 UAV 高移动下的边缘内恢复

结合：



* t-of-M 密钥切换；

* 聚合密文擦除编码；

* 跨 UAV 切换票据；

* 批次标签和重放防护。



***

## 15. 最终闭环汇总

客户端：

$ 
c_{i,0}=b_Eu_i+\Delta(n_i m_i)+e_{i,0},
\qquad
c_{i,1}=a_Eu_i+e_{i,1}.
 $

UAV：

$ 
C_{e,0} =
b_Eu_e+\Delta M_e+e_{e,0},
\qquad
C_{e,1}=a_Eu_e+e_{e,1}.
 $

边缘密钥结构：

$ 
C_{e,0}+C_{e,1}s_E =
\Delta M_e+\delta_{e,E}.
 $

PRE 节点：

$ 
P_{u,e,0} =
\lambda_uC_{e,0} +
\sum_jd_{e,j}\lambda_uk_{j,0,u},
 $

$ 
P_{u,e,1} =
\sum_jd_{e,j}\lambda_uk_{j,1,u}.
 $

云密文：

$ 
C'_{e,0} =
C_{e,0}+\sum_jd_{e,j}k_{j,0},
\qquad
C'_{e,1} =
\sum_jd_{e,j}k_{j,1}.
 $

云解密：

$ 
C'_{e,0}+C'_{e,1}s_C =
\Delta M_e+\delta_{e,E}+\nu_e.
 $

全局：

$ 
C_0^{(C)}+C_1^{(C)}s_C =
\Delta M+\delta,
 $

$ 
M =
\sum_{e=1}^{L}\sum_{i\in S_e}n_i m_i,
 $

$ 
\boxed{
\bar m =
\frac{M}{N},
\qquad
N=\sum_{e=1}^{L}\sum_{i\in S_e}n_i.
}
 $

该方案消除了客户端部分解密份额上传，也不让 UAV 持有或上传私钥相关材料；阈值密钥切换完全位于边缘层内部，云层只解密最终全局聚合结果。