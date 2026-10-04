from __future__ import annotations

import os
import ssl

from collections.abc import Iterable
from typing import Any

from sanic.log import logger


# Only allow secure ciphers, notably leaving out AES-CBC mode
# OpenSSL chooses ECDSA or RSA depending on the cert in use
CIPHERS_TLS12 = [
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES128-GCM-SHA256",
]


def create_context(
    certfile: str | None = None,
    keyfile: str | None = None,
    password: str | None = None,
    purpose: ssl.Purpose = ssl.Purpose.CLIENT_AUTH,
) -> ssl.SSLContext:
    """项目内部接口说明。"""
    context = ssl.create_default_context(purpose=purpose)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(":".join(CIPHERS_TLS12))
    context.set_alpn_protocols(["http/1.1"])
    if purpose is ssl.Purpose.CLIENT_AUTH:
        context.sni_callback = server_name_callback
    if certfile and keyfile:
        context.load_cert_chain(certfile, keyfile, password)
    return context


def shorthand_to_ctx(
    ctxdef: None | ssl.SSLContext | dict | str,
) -> ssl.SSLContext | None:
    """项目内部接口说明。"""
    if ctxdef is None or isinstance(ctxdef, ssl.SSLContext):
        return ctxdef
    if isinstance(ctxdef, str):
        return load_cert_dir(ctxdef)
    if isinstance(ctxdef, dict):
        return CertSimple(**ctxdef)
    raise ValueError(
        f"Invalid ssl argument {type(ctxdef)}."
        " Expecting a list of certdirs, a dict or an SSLContext."
    )


def process_to_context(
    ssldef: None | ssl.SSLContext | dict | str | list | tuple,
) -> ssl.SSLContext | None:
    """项目内部接口说明。"""
    return (
        CertSelector(map(shorthand_to_ctx, ssldef))
        if isinstance(ssldef, (list, tuple))
        else shorthand_to_ctx(ssldef)
    )


def load_cert_dir(p: str) -> ssl.SSLContext:
    if os.path.isfile(p):
        raise ValueError(f"Certificate folder expected but {p} is a file.")
    keyfile = os.path.join(p, "privkey.pem")
    certfile = os.path.join(p, "fullchain.pem")
    if not os.access(keyfile, os.R_OK):
        raise ValueError(
            f"Certificate not found or permission denied {keyfile}"
        )
    if not os.access(certfile, os.R_OK):
        raise ValueError(
            f"Certificate not found or permission denied {certfile}"
        )
    return CertSimple(certfile, keyfile)


def find_cert(self: CertSelector, server_name: str):
    """项目内部接口说明。"""
    if not server_name:
        if self.sanic_fallback:
            return self.sanic_fallback
        raise ValueError(
            "The client provided no SNI to match for certificate."
        )
    for ctx in self.sanic_select:
        if match_hostname(ctx, server_name):
            return ctx
    if self.sanic_fallback:
        return self.sanic_fallback
    raise ValueError(f"No certificate found matching hostname {server_name!r}")


def match_hostname(ctx: ssl.SSLContext | CertSelector, hostname: str) -> bool:
    """项目内部接口说明。"""
    # Local certs are considered trusted, so this can be less pedantic
    # and thus faster than the deprecated ssl.match_hostname function is.
    names = dict(getattr(ctx, "sanic", {})).get("names", [])
    hostname = hostname.lower()
    for name in names:
        if name.startswith("*."):
            if hostname.split(".", 1)[-1] == name[2:]:
                return True
        elif name == hostname:
            return True
    return False


def selector_sni_callback(
    sslobj: ssl.SSLObject, server_name: str, ctx: CertSelector
) -> int | None:
    """项目内部接口说明。"""
    # Call server_name_callback to store the SNI on sslobj
    server_name_callback(sslobj, server_name, ctx)
    # Find a new context matching the hostname
    try:
        sslobj.context = find_cert(ctx, server_name)
    except ValueError as e:
        logger.warning(f"Rejecting TLS connection: {e}")
        # This would show ERR_SSL_UNRECOGNIZED_NAME_ALERT on client side if
        # asyncio/uvloop did proper SSL shutdown. They don't.
        return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
    return None  # mypy complains without explicit return


def server_name_callback(
    sslobj: ssl.SSLObject, server_name: str, ctx: ssl.SSLContext
) -> None:
    """项目内部接口说明。"""
    sslobj.sanic_server_name = server_name  # type: ignore


class SanicSSLContext(ssl.SSLContext):
    sanic: dict[str, os.PathLike]

    @classmethod
    def create_from_ssl_context(cls, context: ssl.SSLContext):
        context.__class__ = cls
        return context


class CertSimple(SanicSSLContext):
    """项目内部接口说明。"""

    sanic: dict[str, Any]

    def __new__(cls, cert, key, **kw):
        # try common aliases, rename to cert/key
        certfile = kw["cert"] = kw.pop("certificate", None) or cert
        keyfile = kw["key"] = kw.pop("keyfile", None) or key
        password = kw.get("password", None)
        if not certfile or not keyfile:
            raise ValueError("SSL dict needs filenames for cert and key.")
        subject = {}
        if "names" not in kw:
            cert = ssl._ssl._test_decode_cert(certfile)  # type: ignore
            kw["names"] = [
                name
                for t, name in cert["subjectAltName"]
                if t in ["DNS", "IP Address"]
            ]
            subject = {k: v for item in cert["subject"] for k, v in item}
        self = create_context(certfile, keyfile, password)
        self.__class__ = cls
        self.sanic = {**subject, **kw}
        return self

    def __init__(self, cert, key, **kw):
        pass  # Do not call super().__init__ because it is already initialized


class CertSelector(ssl.SSLContext):
    """项目内部接口说明。"""

    def __new__(cls, ctxs):
        return super().__new__(cls)

    def __init__(self, ctxs: Iterable[ssl.SSLContext | None]):
        super().__init__()
        self.sni_callback = selector_sni_callback  # type: ignore
        self.sanic_select = []
        self.sanic_fallback = None
        all_names = []
        for i, ctx in enumerate(ctxs):
            if not ctx:
                continue
            names = dict(getattr(ctx, "sanic", {})).get("names", [])
            all_names += names
            self.sanic_select.append(ctx)
            if i == 0:
                self.sanic_fallback = ctx
        if not all_names:
            raise ValueError(
                "No certificates with SubjectAlternativeNames found."
            )
        logger.info(f"Certificate vhosts: {', '.join(all_names)}")
