import { Actor } from 'apify';
import { CheerioCrawler, log } from 'crawlee';
import {
    ALL_BRANDS,
    BASE_URL,
    GENERIC_CODE_PAGES,
    LABELS,
} from './constants.js';
import {
    parseBrandCodesPage,
    parseBrandHomePage,
    parseCodeDetailPage,
    parseGenericCodesPage,
    parseProcedures,
    parseSubmodelPage,
} from './parsers.js';

// ── Init ───────────────────────────────────────────────────────────────
await Actor.init();

// ── Input (Apify console or local INPUT.json in storage) ───────────────
const input = (await Actor.getInput()) ?? {};

const {
    brands: requestedBrands = [],
    scrapeDetails = false,
    scrapeGenericCodes = true,
    scrapeSubmodels = true,
    maxConcurrency = 10,
} = input;

// Determine which brands to scrape
const brandsToScrape =
    requestedBrands.length > 0
        ? Object.fromEntries(
              Object.entries(ALL_BRANDS).filter(([slug]) =>
                  requestedBrands.includes(slug)
              )
          )
        : ALL_BRANDS;

log.info(`Scraping ${Object.keys(brandsToScrape).length} brands`, {
    brands: Object.keys(brandsToScrape),
    scrapeDetails,
    scrapeGenericCodes,
    scrapeSubmodels,
});

// ── Build initial request list ─────────────────────────────────────────
const requests = [];

for (const slug of Object.keys(brandsToScrape)) {
    requests.push({
        url: `${BASE_URL}/${slug}codes/`,
        label: LABELS.BRAND_CODES,
        userData: { brandSlug: slug },
    });
}

if (scrapeSubmodels) {
    for (const slug of Object.keys(brandsToScrape)) {
        requests.push({
            url: `${BASE_URL}/${slug}/`,
            label: LABELS.BRAND_HOME,
            userData: { brandSlug: slug },
        });
    }
}

if (scrapeGenericCodes) {
    for (const [pageSlug, category] of Object.entries(GENERIC_CODE_PAGES)) {
        requests.push({
            url: `${BASE_URL}/${pageSlug}/`,
            label: LABELS.GENERIC_CODES,
            userData: { pageSlug, category },
        });
    }
}
// ── Proxy (Apify platform only) ────────────────────────────────────────
let proxyConfiguration;
try {
    proxyConfiguration = await Actor.createProxyConfiguration({
        useApifyProxy: true,
        apifyProxyGroups: ['SHADER'],
        apifyProxyCountry: 'US',
    });
} catch {
    // Local run — no proxy
}

// ── Crawler ────────────────────────────────────────────────────────────
const crawler = new CheerioCrawler({
    maxConcurrency,
    maxRequestRetries: 3,
    requestHandlerTimeoutSecs: 60,
    ...(proxyConfiguration ? { proxyConfiguration } : {}),

    async requestHandler({ request, body }) {
        const { label } = request;
        const { brandSlug, pageSlug, category, code } = request.userData;

        // Guard: empty body means we likely got a redirect without follow
        if (!body || body.length < 100) {
            throw new Error(`Empty or too-short body (${body?.length ?? 0} bytes) — will retry`);
        }

        switch (label) {
            case LABELS.BRAND_CODES: {
                const codes = parseBrandCodesPage(body, brandSlug);

                if (codes.length === 0) {
                    throw new Error(`[${brandSlug}] No codes parsed from brand codes page — will retry`);
                }

                log.info(`[${brandSlug}] Parsed ${codes.length} codes`);

                await Actor.pushData({
                    type: 'brand_codes',
                    brand: brandSlug,
                    brandName: brandsToScrape[brandSlug],
                    codesCount: codes.length,
                    codes,
                });

                if (scrapeDetails) {
                    const detailRequests = codes
                        .filter((c) => c.detailUrl)
                        .map((c) => ({
                            url: c.detailUrl.startsWith('http')
                                ? c.detailUrl
                                : `${BASE_URL}${c.detailUrl}`,
                            label: LABELS.CODE_DETAIL,
                            userData: { brandSlug, code: c.code },
                        }));

                    if (detailRequests.length > 0) {
                        await crawler.addRequests(detailRequests);
                        log.info(`[${brandSlug}] Enqueued ${detailRequests.length} detail pages`);
                    }
                }
                break;
            }

            case LABELS.CODE_DETAIL: {
                const detail = parseCodeDetailPage(body);
                if (detail) {
                    await Actor.pushData({
                        type: 'code_detail',
                        brand: brandSlug,
                        code,
                        ...detail,
                    });
                    log.debug(`[${brandSlug}] Parsed detail for ${code}`);
                }
                break;
            }

            case LABELS.GENERIC_CODES: {
                const codes = parseGenericCodesPage(body);

                if (codes.length === 0) {
                    throw new Error(`[generic/${pageSlug}] No codes parsed — will retry`);
                }

                log.info(`[generic] Parsed ${codes.length} ${category} codes`);

                await Actor.pushData({
                    type: 'generic_codes',
                    category,
                    pageSlug,
                    codesCount: codes.length,
                    codes,
                });
                break;
            }

            case LABELS.BRAND_HOME: {
                const submodelLinks = parseBrandHomePage(body, brandSlug);
                log.info(`[${brandSlug}] Found ${submodelLinks.length} submodel pages`);

                const submodelRequests = submodelLinks.map((s) => ({
                    url: s.url,
                    label: LABELS.SUBMODEL,
                    userData: { brandSlug, submodelTitle: s.title },
                }));

                if (submodelRequests.length > 0) {
                    await crawler.addRequests(submodelRequests);
                }
                break;
            }

            case LABELS.SUBMODEL: {
                const submodel = parseSubmodelPage(body);
                submodel.url = request.url;

                // Extract procedures (accessing codes, clearing, reset, tests)
                const procedures = parseProcedures(body);

                // If the page has real content but we got nothing, retry
                // (don't retry pages that genuinely have no tables/codes)
                if (
                    submodel.vehicles.length === 0 &&
                    submodel.dtcCodes.length === 0 &&
                    procedures.length === 0 &&
                    !submodel.title
                ) {
                    throw new Error(
                        `[${brandSlug}] Empty submodel parse for ${request.url} — will retry`
                    );
                }

                await Actor.pushData({
                    type: 'submodel',
                    brand: brandSlug,
                    ...submodel,
                });

                // Push procedures as a separate dataset item if any found
                if (procedures.length > 0) {
                    await Actor.pushData({
                        type: 'procedure',
                        brand: brandSlug,
                        url: request.url,
                        title: submodel.title,
                        procedures,
                    });
                    log.debug(
                        `[${brandSlug}] Parsed ${procedures.length} procedures from ${request.url}`
                    );
                }

                log.debug(
                    `[${brandSlug}] Parsed submodel: ${submodel.title} (${submodel.vehicles.length} vehicles, ${submodel.dtcCodes.length} codes)`
                );
                break;
            }

            default:
                log.warning(`Unknown label: ${label}`, { url: request.url });
        }
    },

    async failedRequestHandler({ request }) {
        log.error(`Request failed: ${request.url}`, {
            label: request.label,
            errors: request.errorMessages,
        });
    },
});

// ── Run ────────────────────────────────────────────────────────────────
await crawler.run(requests);

log.info('Done! Data pushed to dataset. Run `npm run build-db` to create the file tree.');

await Actor.exit();
