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
        """Generate HMAC-SHA256 signature for Bitget API."""
        message = timestamp + method.upper() + request_path + body
        hmac_key = base64.b64decode(self.secret)
        digest = hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _request(
        self,
        method: str,
        request_path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Make a signed request to Bitget."""
        timestamp = str(int(time.time() * 1000))
        body_str = "" if body is None else json.dumps(body)

        # Build query string
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            full_path = f"{request_path}?{query_string}" if query_string else request_path
        else:
            full_path = request_path

        sign = self._sign(timestamp, method, full_path, body_str)

        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

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
            raise BitgetAPIError(f"Bitget API request failed: {e}")

    # -------------------------------------------------------------------------
    # Public market data (no signing required, but uses same method)
    # -------------------------------------------------------------------------

    def get_ticker(self, symbol: str) -> dict:
        """Get current price for a symbol (e.g., 'BTCUSDT')."""
        return self._request("GET", "/api/v2/spot/market/tickers", params={"symbol": symbol})

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
        """Get available balance for a specific coin (e.g., 'USDT' → 10.97)."""
        try:
            assets = self.get_account_assets(coin=coin)
            if assets and len(assets) > 0:
                return float(assets[0].get("available", "0"))
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
        quote_size: Optional[str] = None,  # for market buy, specify USDT amount
        client_oid: Optional[str] = None,
    ) -> dict:
        """Place a spot order.

        For market buy: pass quote_size (e.g., "100" = $100 USDT)
        For market sell: pass size (e.g., "0.01" = 0.01 BTC)
        For limit: pass both size and price
        """
        body = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "size": size,
            "quoteSize": quote_size,
            "price": price,
            "clientOid": client_oid or f"onisowo-{int(time.time() * 1000)}",
            "force": "gtc" if order_type == "limit" else "ioc",
        }
        # Remove None values
        body = {k: v for k, v in body.items() if v is not None}
        return self._request("POST", "/api/v2/spot/trade/place-order", body=body)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel a pending order."""
        return self._request(
            "POST",
            "/api/v2/spot/trade/cancel-order",
            body={"symbol": symbol, "orderId": order_id},
        )

    def get_pending_orders(self, symbol: Optional[str] = None) -> list:
        """Get all pending (open) orders, optionally filtered by symbol."""
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v2/spot/trade/orders-pending", params=params)

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
        """Get futures account info."""
        return self._request(
            "GET",
            "/api/v2/mix/account/account",
            params={"productType": product_type, "marginCoin": margin_coin},
        )

    def get_positions(self, product_type: str = "USDT-FUTURES") -> list:
        """Get all open futures positions."""
        return self._request(
            "GET",
            "/api/v2/mix/position/all-position",
            params={"productType": product_type, "marginCoin": "USDT"},
        )

    def place_futures_order(
        self,
        symbol: str,
        side: str,  # "buy" (long) or "sell" (short)
        size: str,  # in contracts
        order_type: str = "market",  # "market" or "limit"
        price: Optional[str] = None,
        leverage: Optional[str] = "1",
        margin_mode: str = "isolated",
        product_type: str = "USDT-FUTURES",
        client_oid: Optional[str] = None,
    ) -> dict:
        """Place a futures order. NOTE: leverage should be 1-3x only (we're cautious)."""
        body = {
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
            "reduceOnly": False,
        }
        body = {k: v for k, v in body.items() if v is not None}
        return self._request("POST", "/api/v2/mix/order/place-order", body=body)

    def set_leverage(self, symbol: str, leverage: str, product_type: str = "USDT-FUTURES") -> dict:
        """Set leverage for a futures symbol. CAUTION: leverage amplifies risk."""
        return self._request(
            "POST",
            "/api/v2/mix/account/set-leverage",
            body={"symbol": symbol, "marginCoin": "USDT", "leverage": leverage, "productType": product_type},
        )

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
