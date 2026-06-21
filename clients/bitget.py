"""
Bitget API client — HMAC-signed REST client for all 58 Bitget Agent Hub APIs.

Reads credentials from env vars. Never hardcodes. Never logs secrets.

Endpoints covered (representative):
- /api/v2/spot/account/assets
- /api/v2/spot/market/tickers
- /api/v2/spot/trade/place-order
- /api/v2/spot/trade/cancel-order
- /api/v2/spot/trade/orders-pending
- /api/v2/mix/account/accounts (futures)
- /api/v2/mix/order/place-order (futures)
- /api/v2/mix/position/all-position
- /api/v2/market/... (public market data)

This client follows Bitget's official signing pattern:
- HMAC-SHA256 of timestamp + method + requestPath + body
- Headers: ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE, Content-Type
"""

import os
import time
import json
import base64
import hmac
import hashlib
from typing import Any, Optional
import requests


class BitgetClient:
    """Bitget REST client with HMAC-SHA256 signing."""

    BASE_URL = "https://api.bitget.com"
    TIMEOUT = 15  # seconds

    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, passphrase: Optional[str] = None):
        self.api_key = api_key or os.environ.get("BITGET_API_KEY", "")
        self.secret = secret or os.environ.get("BITGET_SECRET_KEY", "")
        self.passphrase = passphrase or os.environ.get("BITGET_PASSPHRASE", "")

        if not all([self.api_key, self.secret, self.passphrase]):
            raise ValueError(
                "Bitget credentials missing. Set BITGET_API_KEY, BITGET_SECRET_KEY, "
                "BITGET_PASSPHRASE env vars (or pass to constructor)."
            )

    # -------------------------------------------------------------------------
    # Signing
    # -------------------------------------------------------------------------

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """Generate HMAC-SHA256 signature for Bitget v2 API.

        Bitget's API secret is delivered as a base64-encoded string. The HMAC
        key is the SECRET itself (already the right format), NOT its decoded
        bytes. Earlier version b64-decoded the secret again, which produced
        a wrong key and every signed request was rejected with bad auth.
        """
        message = timestamp + method.upper() + request_path + body
        digest = hmac.new(self.secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _request(
        self,
        method: str,
        request_path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        signed: bool = True,
    ) -> dict[str, Any]:
        """Make a request to Bitget. Public endpoints (market data) work without signing.

        signed=True (default): adds Bitget auth headers. Required for /account,
        /trade, /position endpoints.
        signed=False: no auth. Use for /market/* public endpoints.
        """
        timestamp = str(int(time.time() * 1000))
        body_str = "" if body is None else json.dumps(body)

        # Build query string
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            full_path = f"{request_path}?{query_string}" if query_string else request_path
        else:
            full_path = request_path

        if signed:
            sign = self._sign(timestamp, method, full_path, body_str)
            headers = {
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": sign,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.passphrase,
                "Content-Type": "application/json",
                "locale": "en-US",
            }
        else:
            # Public endpoint - no auth headers needed
            headers = {"Content-Type": "application/json", "locale": "en-US"}

        url = f"{self.BASE_URL}{full_path}"

        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=self.TIMEOUT)
            elif method.upper() == "POST":
                resp = requests.post(url, headers=headers, data=body_str, timeout=self.TIMEOUT)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            resp.raise_for_status()
            data = resp.json()

            # Bitget wraps responses in {code, msg, data, requestTime}
            if data.get("code") != "00000":
                raise BitgetAPIError(
                    f"Bitget API error: code={data.get('code')}, msg={data.get('msg')}"
                )

            return data.get("data", {})

        except requests.exceptions.Timeout:
            raise BitgetAPIError(f"Bitget API timeout: {url}")
        except requests.exceptions.RequestException as e:
            # DEBUG: log + include the exact response body in the error so
            # debugging 400s from a Telegram bot is possible.
            response_body = ""
            try:
                if hasattr(e, 'response') and e.response is not None:
                    response_body = e.response.text[:1000]
                    logger.error(
                        f"Bitget 4xx/5xx for {method} {url}: "
                        f"body={body_str}, response_status={e.response.status_code}, "
                        f"response_body={response_body}"
                    )
                else:
                    logger.error(f"Bitget request error for {method} {url}: body={body_str}, error={e}")
            except Exception:
                pass
            # Include response body in the raised error so it surfaces in
            # the Telegram message — not just the generic "400 Client Error".
            error_msg = f"Bitget API request failed: {e}"
            if response_body:
                error_msg += f" | Response: {response_body}"
            raise BitgetAPIError(error_msg)

    # -------------------------------------------------------------------------
    # Public market data (no signing required, but uses same method)
    # -------------------------------------------------------------------------

    def get_ticker(self, symbol: str) -> dict:
        """Get current price for a symbol (e.g., 'BTCUSDT').

        Tries multiple endpoints in order, all unsigned (public market data):
          1. V3 futures ticker (for USDT pairs on UTA)
          2. V3 spot ticker
          3. V2 spot ticker (last resort)

        Returns the first one that works.
        """
        endpoints = []
        if symbol.endswith("USDT"):
            endpoints = [
                ("GET", "/api/v3/market/tickers", {"symbol": symbol, "category": "USDT-FUTURES"}),
                ("GET", "/api/v3/market/tickers", {"symbol": symbol, "category": "spot"}),
                ("GET", "/api/v2/spot/market/tickers", {"symbol": symbol}),
            ]
        else:
            endpoints = [
                ("GET", "/api/v3/market/tickers", {"symbol": symbol, "category": "spot"}),
                ("GET", "/api/v2/spot/market/tickers", {"symbol": symbol}),
            ]
        last_err = None
        for method, path, params in endpoints:
            try:
                # Public market endpoints don't need auth
                resp = self._request(method, path, params=params, signed=False)
                # _request already returns the inner 'data' field.
                # If it's a list, take the first item (the ticker).
                if isinstance(resp, list) and resp:
                    return resp[0]
                # If it's a dict with 'data' inside (some endpoints)
                if isinstance(resp, dict) and resp.get("data"):
                    inner = resp["data"]
                    return inner[0] if isinstance(inner, list) else inner
                # If it's a non-empty dict (some V3 endpoints return the ticker directly)
                if isinstance(resp, dict) and (resp.get("lastPrice") or resp.get("lastPr") or resp.get("last")):
                    return resp
            except Exception as e:
                last_err = e
                continue
        # If we got here, all endpoints failed. Raise the last error.
        if last_err:
            raise last_err
        return {}

    def get_all_tickers(self) -> list:
        """Get all spot tickers (large response, cache for 30s)."""
        return self._request("GET", "/api/v2/spot/market/tickers")

    def get_candles(self, symbol: str, granularity: str = "1h", limit: int = 100) -> list:
        """Get OHLCV candles. Granularity: 1m, 5m, 15m, 1h, 4h, 1d."""
        return self._request(
            "GET",
            "/api/v2/spot/market/candles",
            params={"symbol": symbol, "granularity": granularity, "limit": str(limit)},
        )

    def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Get current order book for a symbol."""
        return self._request(
            "GET",
            "/api/v2/spot/market/orderbook",
            params={"symbol": symbol, "limit": str(limit)},
        )

    # -------------------------------------------------------------------------
    # Account & balance
    # -------------------------------------------------------------------------

    def get_account_assets(self, coin: Optional[str] = None) -> list:
        """Get all spot account assets. Optionally filter by coin (e.g., 'USDT')."""
        params = {"coin": coin} if coin else None
        return self._request("GET", "/api/v2/spot/account/assets", params=params)

    def get_account_balance(self, coin: str = "USDT") -> float:
        """Get available balance for a specific coin across spot and futures.

        Tries V2 spot first; if 0 or empty, falls back to:
        - V2 futures (USDT-margined perps)
        - V3 unified account (UTA) — `/api/v3/account/assets`
        - V3 spot account — `/api/v3/spot/account/assets`

        This handles all account types: classic spot, classic futures,
        and the new Unified Trading Account (UTA).

        Returns the available balance for the requested coin.
        """
        # 1. V2 spot account
        try:
            assets = self.get_account_assets(coin=coin)
            if assets and len(assets) > 0:
                spot_available = float(assets[0].get("available", "0"))
                if spot_available > 0:
                    return spot_available
        except Exception:
            pass
        # 2. V2 futures account (USDT-margined)
        try:
            url = "/api/v2/mix/account/accounts"
            params = {"productType": "USDT-FUTURES", "marginCoin": coin}
            resp = self._request("GET", url, params=params)
            if isinstance(resp, dict):
                data = resp.get("data", resp)
                if isinstance(data, list) and data:
                    bal = float(data[0].get("available", "0") or 0)
                    if bal > 0:
                        return bal
                if isinstance(data, dict):
                    bal = float(data.get("available", "0") or 0)
                    if bal > 0:
                        return bal
        except Exception:
            pass
        # 3. V3 unified account (UTA) — the new Bitget default for upgraded accounts
        try:
            url = "/api/v3/account/assets"
            params = {"coin": coin}
            resp = self._request("GET", url, params=params)
            if isinstance(resp, dict):
                data = resp.get("data", resp)
                # V3 unified account can return data as either:
                #  - A list of coin balances: [{coin: USDT, available: 10.95, ...}]
                #  - A dict with an 'assets' list nested inside
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("coin", "").upper() == coin.upper():
                            return float(item.get("available", "0") or 0)
                if isinstance(data, dict):
                    # Try assets[] first
                    assets = data.get("assets", [])
                    if isinstance(assets, list):
                        for item in assets:
                            if isinstance(item, dict) and item.get("coin", "").upper() == coin.upper():
                                return float(item.get("available", "0") or 0)
                    # Then top-level available
                    if "available" in data:
                        return float(data.get("available", "0") or 0)
                    # Then usdtEquity
                    if "usdtEquity" in data and coin.upper() == "USDT":
                        return float(data.get("usdtEquity", "0") or 0)
        except Exception:
            pass
        # 4. V3 spot account
        try:
            url = "/api/v3/spot/account/assets"
            params = {"coin": coin}
            resp = self._request("GET", url, params=params)
            if isinstance(resp, dict):
                data = resp.get("data", resp)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("coin", "").upper() == coin.upper():
                            return float(item.get("available", "0") or 0)
        except Exception:
            pass
        return 0.0

    # -------------------------------------------------------------------------
    # Spot trading
    # -------------------------------------------------------------------------

    def place_spot_order(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        order_type: str,  # "market" or "limit"
        size: Optional[str] = None,
        price: Optional[str] = None,
        quote_size: Optional[str] = None,  # for market buy, alias for size (USDT)
        client_oid: Optional[str] = None,
    ) -> dict:
        """Place a spot order.

        Bitget V2 API expects:
          For market buy: pass size = USDT amount (e.g. "100" = spend $100)
          For market sell: pass size = base amount (e.g. "0.01" = sell 0.01 BTC)
          For limit: pass size = base amount + price

        IMPORTANT: For market orders, do NOT include `force` — Bitget rejects it
        with 400 'invalid parameter' for orderType=market. Force is only valid for
        limit orders. quote_size is accepted as an alias for size on market buy
        (kept for backward compatibility) but the canonical field is `size`.
        """
        # Canonical: `size` is USDT for market-buy, base for everything else.
        # We accept `quote_size` as a friendlier alias for market-buy USDT.
        if quote_size is not None and size is None:
            size = quote_size
        # Hard floor: Bitget's minTradeUSDT is 1.0 in the docs, but the
        # account-specific minimum in practice is 1.01. The error code is
        # 45110 'less than the minimum amount 1 USDT' for anything < 1.01.
        # Enforce the real minimum here at the lowest layer so NO caller
        # can ever submit a sub-min order.
        BITGET_REAL_MIN_USDT = 1.01
        if order_type == "market" and side.lower() == "buy" and size is not None:
            try:
                size_f = float(size)
                if size_f < BITGET_REAL_MIN_USDT:
                    size = str(BITGET_REAL_MIN_USDT)
            except (TypeError, ValueError):
                pass
        # Normalize the size string for Bitget: trim trailing .0 and ensure
        # it's a string. Bitget's "1.0" sometimes gets parsed oddly, while
        # "1" works reliably.
        if size is not None and isinstance(size, str):
            try:
                size_f = float(size)
                if size_f == int(size_f):
                    size = str(int(size_f))
            except (TypeError, ValueError):
                pass
        body = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "size": size,
            "price": price,
            "clientOid": client_oid or f"onisowo-{int(time.time() * 1000)}",
        }
        # `force` is only valid for limit orders. Bitget rejects with 400
        # if `force` is included with orderType=market.
        if order_type == "limit" and price is not None:
            body["force"] = "gtc"
        # Remove None values so we never send null fields.
        body = {k: v for k, v in body.items() if v is not None}
        # Try V3 (UTA) first, fall back to V2 for classic accounts
        try:
            return self._request("POST", "/api/v3/trade/place-order", body=body)
        except BitgetAPIError as e:
            err = str(e)
            if "404" in err or "not found" in err.lower() or "NOT FOUND" in err:
                return self._request("POST", "/api/v2/spot/trade/place-order", body=body)
            raise

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel a pending order. Tries V3 then V2."""
        body = {"symbol": symbol, "orderId": order_id}
        try:
            return self._request("POST", "/api/v3/trade/cancel-order", body=body)
        except BitgetAPIError as e:
            if "404" in str(e) or "NOT FOUND" in str(e):
                return self._request("POST", "/api/v2/spot/trade/cancel-order", body=body)
            raise

    def get_pending_orders(self, symbol: Optional[str] = None) -> list:
        """Get all pending (open) orders, optionally filtered by symbol."""
        params = {"symbol": symbol} if symbol else None
        # Try V3 first for UTA, fall back to V2
        try:
            return self._request("GET", "/api/v3/trade/orders-pending", params=params)
        except BitgetAPIError as e:
            if "404" in str(e) or "NOT FOUND" in str(e):
                return self._request("GET", "/api/v2/spot/trade/orders-pending", params=params)
            raise

    def get_order_history(self, symbol: str, limit: int = 50) -> list:
        """Get recent order history for a symbol."""
        return self._request(
            "GET",
            "/api/v2/spot/trade/orders-history",
            params={"symbol": symbol, "limit": str(limit)},
        )

    # -------------------------------------------------------------------------
    # Futures (perps) trading
    # -------------------------------------------------------------------------

    def get_futures_account(self, product_type: str = "USDT-FUTURES", margin_coin: str = "USDT") -> dict:
        """Get futures account info. Tries V3 (UTA) then V2."""
        params = {"productType": product_type, "marginCoin": margin_coin}
        try:
            return self._request("GET", "/api/v3/account/account", params=params)
        except BitgetAPIError as e:
            if "404" in str(e) or "NOT FOUND" in str(e):
                return self._request("GET", "/api/v2/mix/account/account", params=params)
            raise

    def get_positions(self, product_type: str = "USDT-FUTURES") -> list:
        """Get all open futures positions. Tries V3 then V2."""
        params = {"productType": product_type, "marginCoin": "USDT"}
        try:
            return self._request("GET", "/api/v3/position/all-position", params=params)
        except BitgetAPIError as e:
            if "404" in str(e) or "NOT FOUND" in str(e):
                return self._request("GET", "/api/v2/mix/position/all-position", params=params)
            raise

    def place_futures_order(
        self,
        symbol: str,
        side: str,  # "buy" (long) or "sell" (short)
        size: str,  # in contracts
        order_type: str = "market",  # "market" or "limit"
        price: Optional[str] = None,
        leverage: Optional[str] = "1",
        margin_mode: str = "crossed",
        product_type: str = "USDT-FUTURES",
        client_oid: Optional[str] = None,
    ) -> dict:
        """Place a futures order.

        Tries V3 (UTA) first, falls back to V2 for classic accounts.
        V3 needs `category` field (e.g. 'linear' for USDT-margined perps).
        V2 uses `productType` (e.g. 'USDT-FUTURES').
        """
        # V3 UTA body: uses posSide (not positionType).
        # posSide: 'long' or 'short' for one-way mode.
        # side: 'buy' or 'sell' (the action).
        # Bitget UTA V3 spec: /api/v3/trade/place-order
        #   {category, symbol, orderType, qty, price, side, posSide, ...}
        v3_body = {
            "category": "USDT-FUTURES",
            "symbol": symbol,
            "marginMode": margin_mode,
            "marginCoin": "USDT",
            "qty": size,
            "side": side,                  # 'buy' to open, 'sell' to close
            "posSide": "long" if side == "buy" else "short",  # V3 needs this
            "orderType": order_type,
            "price": price,
            "leverage": leverage,
            "clientOid": client_oid or f"onisowo-fut-{int(time.time() * 1000)}",
        }
        v3_body = {k: v for k, v in v3_body.items() if v is not None}

        # V2 body: uses productType
        v2_body = {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": "USDT",
            "size": size,
            "side": side,
            "orderType": order_type,
            "price": price,
            "leverage": leverage,
            "clientOid": client_oid or f"onisowo-fut-{int(time.time() * 1000)}",
        }
        v2_body = {k: v for k, v in v2_body.items() if v is not None}
        # Try V3 (UTA) first
        try:
            return self._request("POST", "/api/v3/trade/place-order", body=v3_body)
        except BitgetAPIError as e:
            err = str(e)
            # If V3 says we're in classic mode, fall back to V2
            if "404" in err or "not found" in err.lower() or "NOT FOUND" in err:
                return self._request("POST", "/api/v2/mix/order/place-order", body=v2_body)
            raise

    def place_strategy_order(
        self,
        symbol: str,
        side: str,  # 'buy' or 'sell'
        pos_side: str,  # 'long' or 'short'
        order_type: str,  # 'market' or 'limit'
        qty: str,
        tp_price: Optional[str] = None,  # take profit price
        sl_price: Optional[str] = None,  # stop loss price
        tp_trigger_price: Optional[str] = None,
        sl_trigger_price: Optional[str] = None,
        leverage: Optional[str] = "5",
        margin_mode: str = "crossed",
        margin_coin: str = "USDT",
        client_oid: Optional[str] = None,
    ) -> dict:
        """Place a futures order with TP/SL attached. V3 UTA endpoint.

        POST /api/v3/trade/place-strategy-order

        The TP/SL are attached to the order itself, so when the position
        opens, both take profit and stop loss are already set.
        """
        body = {
            "category": "USDT-FUTURES",
            "symbol": symbol,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "qty": qty,
            "side": side,
            "posSide": pos_side,
            "orderType": order_type,
            "leverage": leverage,
            "clientOid": client_oid or f"onisowo-strat-{int(time.time() * 1000)}",
        }
        # Attach TP/SL if provided
        if tp_price is not None or tp_trigger_price is not None:
            body["tpPrice"] = tp_price or ""
            body["tpTriggerPrice"] = tp_trigger_price or ""
        if sl_price is not None or sl_trigger_price is not None:
            body["slPrice"] = sl_price or ""
            body["slTriggerPrice"] = sl_trigger_price or ""
        body = {k: v for k, v in body.items() if v != ""}
        try:
            return self._request("POST", "/api/v3/trade/place-strategy-order", body=body)
        except BitgetAPIError as e:
            # If V3 strategy endpoint doesn't exist, fall back to V2
            if "404" in str(e) or "NOT FOUND" in str(e):
                # Convert to V2 format and use /api/v2/mix/order/placeOrder
                v2_body = {
                    "symbol": symbol,
                    "productType": "USDT-FUTURES",
                    "marginMode": margin_mode,
                    "marginCoin": margin_coin,
                    "size": qty,
                    "side": side,
                    "orderType": order_type,
                    "leverage": leverage,
                    "clientOid": body.get("clientOid"),
                }
                if tp_trigger_price:
                    v2_body["tpTriggerPrice"] = tp_trigger_price
                if sl_trigger_price:
                    v2_body["slTriggerPrice"] = sl_trigger_price
                if tp_price:
                    v2_body["tpPrice"] = tp_price
                if sl_price:
                    v2_body["slPrice"] = sl_price
                v2_body = {k: v for k, v in v2_body.items() if v is not None}
                return self._request("POST", "/api/v2/mix/order/placeOrder", body=v2_body)
            raise

    def set_leverage(self, symbol: str, leverage: str, product_type: str = "USDT-FUTURES") -> dict:
        """Set leverage for a futures symbol. CAUTION: leverage amplifies risk.
        Tries V3 (UTA) then V2 (classic)."""
        body = {"symbol": symbol, "marginCoin": "USDT", "leverage": leverage, "productType": product_type}
        try:
            return self._request("POST", "/api/v3/account/set-leverage", body=body)
        except BitgetAPIError as e:
            if "404" in str(e) or "NOT FOUND" in str(e):
                return self._request("POST", "/api/v2/mix/account/set-leverage", body=body)
            raise

    # -------------------------------------------------------------------------
    # Convenience methods (used by agent / skills)
    # -------------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Quick health check. Returns True if account is accessible."""
        try:
            assets = self.get_account_assets()
            return isinstance(assets, list)
        except Exception:
            return False

    def get_portfolio_value_usdt(self) -> float:
        """Estimate total portfolio value in USDT (rough — uses cached prices)."""
        try:
            assets = self.get_account_assets()
            total = 0.0
            for asset in assets:
                coin = asset.get("coin", "").upper()
                available = float(asset.get("available", "0"))
                if coin == "USDT":
                    total += available
                elif available > 0 and coin in ("BTC", "ETH", "SOL", "USDC"):
                    try:
                        ticker = self.get_ticker(f"{coin}USDT")
                        price = float(ticker.get("lastPr", "0")) if isinstance(ticker, dict) else 0
                        if price == 0 and isinstance(ticker, list) and len(ticker) > 0:
                            price = float(ticker[0].get("lastPr", "0"))
                        total += available * price
                    except Exception:
                        pass  # skip if can't get price
            return total
        except Exception:
            return 0.0


class BitgetAPIError(Exception):
    """Raised when Bitget API returns an error."""
    pass
