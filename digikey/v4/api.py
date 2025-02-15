import logging
import sys
from typing import Dict, Optional, Any, List
import digikey.oauth.oauth2
from digikey.exceptions import DigikeyError
from digikey.v4.productinformation import (
    KeywordRequest,
    KeywordResponse,
    ProductDetails,
    DigiReelPricing,
    ProductSearchApi,
    Configuration as ProductInfoConfiguration,
)
from digikey.v4.productinformation.rest import ApiException as ProductInfoApiException
from digikey.v4.ordersupport import (
    OrderStatusResponse,
    SalesOrderHistoryItem,
    OrderDetailsApi,
    Configuration as OrderDetailsConfiguration,
)
from digikey.v4.batchproductdetails import (
    BatchProductDetailsRequest,
    BatchProductDetailsResponse,
    BatchSearchApi,
    Configuration as BatchSearchConfiguration,
)

logger = logging.getLogger(__name__)


class _DigikeyApiWrapper:
    """
    Internal wrapper class for the Digikey API.  Handles authentication and API calls.
    """

    def __init__(
        self,
        wrapped_function_name: str,
        module: Any,  # digikey.v4.productinformation or digikey.v4.ordersupport or digikey.v4.batchproductdetails
        client_id: str,
        client_secret: str,
        storage_path: str,
        client_sandbox: bool = False,
    ):
        """
        Initializes the Digikey API wrapper.

        Args:
            wrapped_function_name: The name of the API function to wrap (e.g., 'keyword_search_with_http_info').
            module: The Digikey API module (e.g., digikey.v4.productinformation).
            client_id: The Digikey Client ID.
            client_secret: The Digikey Client Secret.
            storage_path: The path to the directory where the token storage file will be saved.
            client_sandbox: Whether to use the Digikey sandbox API (default: False).
        """
        self.sandbox = client_sandbox

        apinames = {
            digikey.v4.productinformation: "products",
            digikey.v4.ordersupport: "OrderDetails",
            digikey.v4.batchproductdetails: "BatchSearch",
        }

        apiclasses = {
            digikey.v4.productinformation: ProductSearchApi,
            digikey.v4.ordersupport: OrderDetailsApi,
            digikey.v4.batchproductdetails: BatchSearchApi,
        }

        apiname = apinames[module]
        apiclass = apiclasses[module]

        # Configure API key authorization: apiKeySecurity
        if module == digikey.v4.productinformation:
            configuration = ProductInfoConfiguration()
        elif module == digikey.v4.ordersupport:
            configuration = OrderDetailsConfiguration()
        elif module == digikey.v4.batchproductdetails:
            configuration = BatchSearchConfiguration()
        else:
            raise ValueError("Invalid module provided")

        configuration.api_key["X-DIGIKEY-Client-Id"] = client_id

        if client_id is None or client_secret is None:
            raise DigikeyError(
                "Please provide a valid DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET"
            )

        # Use normal API by default, if DIGIKEY_CLIENT_SANDBOX is True use sandbox API
        configuration.host = f"https://api.digikey.com/{apiname}/v4"
        if client_sandbox:
            configuration.host = f"https://sandbox-api.digikey.com/{apiname}/v4"
            self.sandbox = True

        # Configure OAuth2 access token for authorization: oauth2AccessCodeSecurity
        self._digikeyApiToken = digikey.oauth.oauth2.TokenHandler(
            a_id=client_id,
            a_secret=client_secret,
            a_token_storage_path=storage_path,
            version=3,
            sandbox=self.sandbox,
        ).get_access_token()
        configuration.access_token = self._digikeyApiToken.access_token

        # create an instance of the API class
        self._api_instance = apiclass(module.ApiClient(configuration))

        # Populate reused ids
        self.authorization = self._digikeyApiToken.get_authorization()
        self.x_digikey_client_id = client_id
        self.wrapped_function_name = wrapped_function_name

    @staticmethod
    def _remaining_requests(
        header: Dict[str, str], api_limits: Optional[Dict[str, int]]
    ):
        """
        Extracts and logs rate limit information from the API response header.

        Args:
            header: The API response header.
            api_limits: A dictionary to store the API request limits.
        """
        try:
            rate_limit = header["X-RateLimit-Limit"]
            rate_limit_rem = header["X-RateLimit-Remaining"]

            if api_limits is not None and isinstance(api_limits, dict):
                api_limits["api_requests_limit"] = int(rate_limit)
                api_limits["api_requests_remaining"] = int(rate_limit_rem)

            logger.debug(
                "Requests remaining: [{}/{}]".format(rate_limit_rem, rate_limit)
            )
        except (KeyError, ValueError) as e:
            logger.debug(f"No api limits returned -> {e.__class__.__name__}: {e}")
            if api_limits is not None and isinstance(api_limits, dict):
                api_limits["api_requests_limit"] = None
                api_limits["api_requests_remaining"] = None

    @staticmethod
    def _store_api_statuscode(statuscode: int, status: Optional[Dict[str, int]]):
        """
        Stores the API status code in the provided dictionary.

        Args:
            statuscode: The API status code.
            status: A dictionary to store the status code.
        """
        if status is not None and isinstance(status, dict):
            status["code"] = int(statuscode)

        logger.debug("API returned code: {}".format(statuscode))

    def call_api_function(
        self,
        *args: Any,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Calls the wrapped Digikey API function.

        Args:
            *args: Positional arguments to pass to the API function.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.
            **kwargs: Keyword arguments to pass to the API function.

        Returns:
            The API response.
        """
        try:
            func = getattr(self._api_instance, self.wrapped_function_name)
            logger.debug(f"CALL wrapped -> {func.__qualname__}")
            api_response = func(
                *args,
                self.x_digikey_client_id,
                authorization=self.authorization,
                **kwargs,
            )
            self._remaining_requests(api_response[2], api_limits)
            self._store_api_statuscode(api_response[1], status)

            return api_response[0]
        except ProductInfoApiException as e:
            logger.error(f"Exception when calling {self.wrapped_function_name}: {e}")
            self._store_api_statuscode(e.status, status)
            raise
        except Exception as e:
            logger.error(
                f"Unexpected exception when calling {self.wrapped_function_name}: {e}"
            )
            raise


class DigikeyApi:
    """
    Public API class for interacting with the Digikey API.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        storage_path: str = "/tmp/",
        client_sandbox: bool = False,
    ):
        """
        Initializes the Digikey API client.

        Args:
            client_id: The Digikey Client ID.
            client_secret: The Digikey Client Secret.
            storage_path: The path to the directory where the token storage file will be saved.
            client_sandbox: Whether to use the Digikey sandbox API (default: False).
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.storage_path = storage_path
        self.client_sandbox = client_sandbox

    def keyword_search(
        self,
        body: KeywordRequest,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> KeywordResponse:
        """
        Performs a keyword search for products.

        Args:
            body: The KeywordRequest object containing the search parameters.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            The KeywordResponse object containing the search results.
        """
        client = _DigikeyApiWrapper(
            "keyword_search_with_http_info",
            digikey.v4.productinformation,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )

        assert isinstance(body, KeywordRequest)
        return client.call_api_function(body=body, api_limits=api_limits, status=status)

    def product_details(
        self,
        digikey_part_number: str,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> ProductDetails:
        """
        Retrieves detailed product information for a given Digikey part number.

        Args:
            digikey_part_number: The Digikey part number.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            The ProductDetails object containing the product information.
        """
        client = _DigikeyApiWrapper(
            "product_details_with_http_info",
            digikey.v4.productinformation,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )
        assert isinstance(digikey_part_number, str)
        return client.call_api_function(
            digikey_part_number, api_limits=api_limits, status=status
        )

    def digi_reel_pricing(
        self,
        digikey_part_number: str,
        quantity: int,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> DigiReelPricing:
        """
        Calculates the Digi-Reel pricing for a given part number and quantity.

        Args:
            digikey_part_number: The Digikey part number.
            quantity: The quantity.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            The DigiReelPricing object containing the pricing information.
        """
        client = _DigikeyApiWrapper(
            "digi_reel_pricing_with_http_info",
            digikey.v4.productinformation,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )

        assert isinstance(digikey_part_number, str)
        assert isinstance(quantity, int)
        return client.call_api_function(
            digikey_part_number, quantity, api_limits=api_limits, status=status
        )

    def suggested_parts(
        self,
        digikey_part_number: str,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> ProductDetails:
        """
        Retrieves detailed product information and two suggested products for a given part number.

        Args:
            digikey_part_number: The Digikey part number.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            The ProductDetails object containing the product information and suggested parts.
        """
        client = _DigikeyApiWrapper(
            "suggested_parts_with_http_info",
            digikey.v4.productinformation,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )

        assert isinstance(digikey_part_number, str)
        return client.call_api_function(
            digikey_part_number, api_limits=api_limits, status=status
        )

    def status_salesorder_id(
        self,
        sales_order_id: str,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> OrderStatusResponse:
        """
        Retrieves the order status for a given sales order ID.

        Args:
            sales_order_id: The sales order ID.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            The OrderStatusResponse object containing the order status information.
        """
        client = _DigikeyApiWrapper(
            "order_status_with_http_info",
            digikey.v4.ordersupport,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )

        assert isinstance(sales_order_id, str)
        return client.call_api_function(
            sales_order_id, api_limits=api_limits, status=status
        )

    def salesorder_history(
        self,
        start_date: str,
        end_date: str,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> List[SalesOrderHistoryItem]:
        """
        Retrieves the sales order history for a given date range.

        Args:
            start_date: The start date (YYYY-MM-DD).
            end_date: The end date (YYYY-MM-DD).
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            A list of SalesOrderHistoryItem objects containing the sales order history.
        """
        client = _DigikeyApiWrapper(
            "order_history_with_http_info",
            digikey.v4.ordersupport,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )

        assert isinstance(start_date, str)
        assert isinstance(end_date, str)
        return client.call_api_function(
            start_date=start_date,
            end_date=end_date,
            api_limits=api_limits,
            status=status,
        )

    def batch_product_details(
        self,
        body: BatchProductDetailsRequest,
        api_limits: Optional[Dict[str, int]] = None,
        status: Optional[Dict[str, int]] = None,
    ) -> BatchProductDetailsResponse:
        """
        Retrieves product details in batch.

        Args:
            body: The BatchProductDetailsRequest object containing the list of products to search for.
            api_limits: Optional dictionary to store API rate limits.
            status: Optional dictionary to store API status code.

        Returns:
            The BatchProductDetailsResponse object containing the batch product details.
        """
        client = _DigikeyApiWrapper(
            "batch_product_details_with_http_info",
            digikey.v4.batchproductdetails,
            self.client_id,
            self.client_secret,
            self.storage_path,
            self.client_sandbox,
        )

        assert isinstance(body, BatchProductDetailsRequest)
        return client.call_api_function(body=body, api_limits=api_limits, status=status)


if __name__ == "__main__":

    def _main():
        client_id = "id"
        client_secret = "secret"

        digikey_api = DigikeyApi(client_id, client_secret, storage_path="/tmp/")

        # Example 1: Keyword Search
        keyword_request = KeywordRequest(keywords="raspberry pi")
        keyword_response = digikey_api.keyword_search(keyword_request)
        print("Keyword Search Results:", keyword_response)

        # Example 2: Product Details
        part_number = "296-24647-1-ND"  # Example part number
        product_details = digikey_api.product_details(part_number)
        print("Product Details:", product_details)

    sys.exit(_main())
