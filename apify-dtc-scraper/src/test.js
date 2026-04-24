/**
 * Quick smoke test — fetches a few real pages and validates parsing.
 * Run with: node src/test.js
 */
import {
    parseBrandCodesPage,
    parseBrandHomePage,
    parseCodeDetailPage,
    parseGenericCodesPage,
    parseSubmodelPage,
} from './parsers.js';

const BASE = 'https://www.troublecodes.net';

async function fetchHTML(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${url}`);
    return res.text();
}

async function test(name, fn) {
    try {
        await fn();
        console.log(`  ✓ ${name}`);
    } catch (e) {
        console.error(`  ✗ ${name}: ${e.message}`);
        process.exitCode = 1;
    }
}

function assert(condition, msg) {
    if (!condition) throw new Error(msg);
}

console.log('\n─── DTC Scraper Parser Tests ───\n');

// Test 1: Ford codes listing
await test('parseBrandCodesPage — Ford', async () => {
    const html = await fetchHTML(`${BASE}/fordcodes/`);
    const codes = parseBrandCodesPage(html, 'ford');
    assert(codes.length > 100, `Expected >100 Ford codes, got ${codes.length}`);

    const p0171 = codes.find((c) => c.code === 'P0171');
    assert(p0171, 'Should find P0171');
    assert(p0171.faultLocation.length > 5, 'P0171 should have a fault description');
    console.log(`    → ${codes.length} Ford codes parsed`);
    console.log(`    → Sample: ${p0171.code} — ${p0171.faultLocation}`);
});

// Test 2: Generic P-codes
await test('parseGenericCodesPage — pcodes', async () => {
    const html = await fetchHTML(`${BASE}/pcodes/`);
    const codes = parseGenericCodesPage(html);
    assert(codes.length > 500, `Expected >500 generic P codes, got ${codes.length}`);

    const p0001 = codes.find((c) => c.code === 'P0001');
    assert(p0001, 'Should find P0001');
    console.log(`    → ${codes.length} generic P codes parsed`);
});

// Test 3: Code detail page
await test('parseCodeDetailPage — Ford P0171', async () => {
    const html = await fetchHTML(`${BASE}/fordcodes/p0171/`);
    const detail = parseCodeDetailPage(html);
    assert(detail, 'Should parse detail page');
    assert(detail.title.includes('P0171'), 'Title should contain code');
    assert(detail.fullText.length > 200, 'Should have substantial content');
    console.log(`    → Title: ${detail.title.substring(0, 80)}...`);
    console.log(`    → Sections: ${Object.keys(detail.sections).join(', ')}`);
});

// Test 4: Brand home page (submodel links)
await test('parseBrandHomePage — Toyota', async () => {
    const html = await fetchHTML(`${BASE}/toyota/`);
    const submodels = parseBrandHomePage(html, 'toyota');
    assert(submodels.length > 5, `Expected >5 Toyota submodel links, got ${submodels.length}`);
    console.log(`    → ${submodels.length} submodel pages found`);
    console.log(`    → Sample: ${submodels[0].title}`);
});

// Test 5: Submodel page
await test('parseSubmodelPage — Toyota submodel', async () => {
    const html = await fetchHTML(
        `${BASE}/toyota/4runner-camry-highlander-sienna-yaris-2003-2009/`
    );
    const result = parseSubmodelPage(html);
    assert(result.vehicles.length > 0, 'Should find vehicle entries');
    console.log(`    → ${result.vehicles.length} vehicles, ${result.dtcCodes.length} codes`);
    if (result.vehicles[0]) {
        console.log(
            `    → Sample: ${result.vehicles[0].model} ${result.vehicles[0].year} ${result.vehicles[0].engine}`
        );
    }
});

// Test 6: Multiple brands quick check
await test('Multiple brands have codes', async () => {
    const brands = ['honda', 'bmw', 'toyota'];
    for (const brand of brands) {
        const html = await fetchHTML(`${BASE}/${brand}codes/`);
        const codes = parseBrandCodesPage(html, brand);
        assert(codes.length > 10, `${brand} should have >10 codes, got ${codes.length}`);
        console.log(`    → ${brand}: ${codes.length} codes`);
    }
});

console.log('\n─── All tests complete ───\n');
