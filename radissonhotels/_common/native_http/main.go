// TLS/H2 transport. Python owns parameters, collection and storage.
// Stdout contains RAM-only response envelopes and must never be logged.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"net/url"
	"os"
	"strings"

	http "github.com/bogdanfinn/fhttp"
	tlsclient "github.com/bogdanfinn/tls-client"
	"github.com/bogdanfinn/tls-client/profiles"
)

type input struct {
	Method   string      `json:"method"`
	URL      string      `json:"url"`
	Headers  [][2]string `json:"headers"`
	Body     string      `json:"body"`
	ProxyURL string      `json:"proxy_url"`
}

type output struct {
	Status      int                 `json:"status"`
	Headers     map[string][]string `json:"headers,omitempty"`
	Body        string              `json:"body,omitempty"`
	Cookies     []*http.Cookie      `json:"cookies,omitempty"`
	HTTPVersion string              `json:"http_version,omitempty"`
	ALPN        string              `json:"alpn,omitempty"`
	TLSVersion  uint16              `json:"tls_version,omitempty"`
	ErrorType   string              `json:"error_type,omitempty"`
}

func inScope(method, value string) bool {
	u, err := url.Parse(value)
	if err != nil || u.Scheme != "https" || u.Host != "www.radissonhotels.com" || u.User != nil || u.Fragment != "" {
		return false
	}
	if method == "GET" {
		return u.Path == "/zh-cn/" || strings.HasPrefix(u.Path, "/iFKu8e/") ||
			strings.HasPrefix(u.Path, "/zimba-api/") || strings.HasPrefix(u.Path, "/content-api/hotels/") ||
			strings.HasPrefix(u.Path, "/content-api/currency/country/") ||
			u.Path == "/availability-api/calendar-from"
	}
	return method == "POST" && (strings.HasPrefix(u.Path, "/iFKu8e/") ||
		u.Path == "/availability-api/stays-by-rooms" || u.Path == "/availability-api/multiple-hotels-rates")
}

func main() {
	reader := bufio.NewReaderSize(os.Stdin, 65536)
	decoder, encoder := json.NewDecoder(reader), json.NewEncoder(os.Stdout)
	var client tlsclient.HttpClient
	var proxyValue string
	requests := 0
	defer func() {
		if client != nil {
			client.CloseIdleConnections()
		}
	}()
	for {
		var job input
		if err := decoder.Decode(&job); err != nil {
			return
		}
		job.Method = strings.ToUpper(job.Method)
		if !inScope(job.Method, job.URL) || len(job.Body) > 65536 || len(job.Headers) > 64 || requests >= 24 {
			_ = encoder.Encode(output{ErrorType: "ScopeError"})
			continue
		}
		if client == nil {
			options := []tlsclient.HttpClientOption{
				tlsclient.WithClientProfile(profiles.Chrome_152),
				tlsclient.WithRandomTLSExtensionOrder(),
				tlsclient.WithTimeoutSeconds(20),
				tlsclient.WithNotFollowRedirects(),
				tlsclient.WithDisableHttp3(),
				tlsclient.WithCookieJar(tlsclient.NewCookieJar()),
			}
			if job.ProxyURL != "" {
				options = append(options, tlsclient.WithProxyUrl(job.ProxyURL))
			}
			var err error
			client, err = tlsclient.NewHttpClient(tlsclient.NewNoopLogger(), options...)
			if err != nil {
				_ = encoder.Encode(output{ErrorType: "ClientInitError"})
				return
			}
			proxyValue = job.ProxyURL
		} else if job.ProxyURL != proxyValue {
			_ = encoder.Encode(output{ErrorType: "ProxyChangeError"})
			continue
		}
		request, err := http.NewRequest(job.Method, job.URL, bytes.NewBufferString(job.Body))
		if err != nil {
			_ = encoder.Encode(output{ErrorType: "RequestInitError"})
			continue
		}
		order := make([]string, 0, len(job.Headers))
		for _, pair := range job.Headers {
			name := strings.ToLower(pair[0])
			if strings.HasPrefix(name, ":") || name == "host" || name == "content-length" || name == "connection" ||
				name == "authorization" || name == "proxy-authorization" || strings.ContainsAny(pair[1], "\r\n") {
				continue
			}
			request.Header[name] = append(request.Header[name], pair[1])
			order = append(order, name)
		}
		request.Header[http.HeaderOrderKey] = order
		requests++
		response, err := client.Do(request)
		if err != nil {
			_ = encoder.Encode(output{ErrorType: "TransportError"})
			continue
		}
		if !response.Uncompressed {
			response.Body = http.DecompressBodyByType(response.Body, response.Header.Get("Content-Encoding"))
		}
		body, err := io.ReadAll(io.LimitReader(response.Body, 4000001))
		_ = response.Body.Close()
		if err != nil || len(body) > 4000000 {
			_ = encoder.Encode(output{ErrorType: "ResponseReadError"})
			continue
		}
		result := output{Status: response.StatusCode, Headers: response.Header, Body: string(body),
			HTTPVersion: response.Proto, Cookies: client.GetCookies(request.URL)}
		if response.TLS != nil {
			result.ALPN, result.TLSVersion = response.TLS.NegotiatedProtocol, response.TLS.Version
		}
		_ = encoder.Encode(result)
	}
}
