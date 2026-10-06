"""云元数据字面量 100.100.100.200 从 IP 门溜走 —— 「元数据永不放行」在默认路径不成立。

症状（2026-10-06 实测，纯函数判定，零网络）：

    PASSED <- http://100.100.100.200/
    PASSED <- http://100.100.100.200/latest/meta-data/instance-id/
    PASSED <- http://1684301000/        （十进制，归一后是同一个地址）
    PASSED <- http://0x646464c8/        （十六进制，同上）
    PASSED <- http://metadata/latest/meta-data/   （裸主机名）

根因的形状：`100.100.100.200` 是阿里云 IMDS，住在 100.64.0.0/10（CGNAT），
这一段不在 `_PRIVATE_NETWORKS` 里 —— is_ip 分支的私网循环对它不设防，
`_METADATA_HOSTS` 只接在 allowlist 询问那一半接线上，而 allowlist 分支
只有在地址命中私网段时才被询问，于是它永远轮不到被问。裸主机名 `metadata`
则在 `_BLOCKED_HOSTNAMES` 之外，hostname 分支同样放行。

判据：每种写法都 SecurityError；把 IMDS 点名进 allowlist 也不开（元数据
不属于「我的 dev 服务」）；`_METADATA_HOSTS` 的每个成员都各自被默认路径拒绝。
真实抓 IMDS 需真云网络，不在本测试的射程内 —— 断言停在路径判定层。
"""

from __future__ import annotations

import pytest

from dhole_mcp.security import (
    _METADATA_HOSTS,
    SecurityError,
    allow_private_scope,
    validate_url,
)

IMDS_SPELLINGS = [
    "http://100.100.100.200/",
    "http://100.100.100.200/latest/meta-data/instance-id/",
    "http://1684301000/",  # decimal integer -> 100.100.100.200
    "http://0x646464c8/",  # hex integer -> 100.100.100.200
]


@pytest.mark.parametrize("url", IMDS_SPELLINGS)
def test_the_aliyun_imds_literal_is_refused_in_every_spelling(url):
    with pytest.raises(SecurityError, match="metadata"):
        validate_url(url)


def test_the_bare_metadata_hostname_is_refused_too():
    with pytest.raises(SecurityError, match="metadata"):
        validate_url("http://metadata/latest/meta-data/")


@pytest.mark.parametrize("url", IMDS_SPELLINGS)
def test_naming_the_imds_in_the_allowlist_does_not_open_it(url):
    with allow_private_scope(["100.100.100.200"]):
        with pytest.raises(SecurityError, match="metadata"):
            validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
    ],
)
def test_every_metadata_endpoint_member_is_refused_by_the_default_path(url):
    assert len(_METADATA_HOSTS) == 5  # 新成员进清单时，这张表跟着长
    with pytest.raises(SecurityError):
        validate_url(url)
