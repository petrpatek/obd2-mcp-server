import * as cheerio from 'cheerio';

/**
 * Parse a brand codes listing page (e.g. /fordcodes/)
 * Returns array of { code, faultLocation, probableCause, detailUrl }
 *
 * The HTML is often a single compressed line with malformed nesting like:
 *   <tr><tr><td>...</td><td>...</td><td>...</td></tr>
 * so we can't rely on proper <tr>...</tr> boundaries.
 * Instead we find all <td> groups of 3 that contain a DTC code.
 */
export function parseBrandCodesPage(html, brandSlug) {
    const $ = cheerio.load(html);
    const codes = [];
    const existingCodes = new Set();

    // Strategy: get all <td> elements inside the table, group them by 3
    const allTds = $('table td');
    const tdArray = [];
    allTds.each((_, el) => tdArray.push($(el)));

    // Walk through tds looking for groups that start with a DTC code
    for (let i = 0; i < tdArray.length; i++) {
        const cell = tdArray[i];
        const codeLink = cell.find('a');
        const rawText = (codeLink.text() || cell.text()).trim();

        const codeMatch = rawText.match(/^([PBCU]\w{4,5})\s*$/i);
        if (!codeMatch) continue;

        const code = codeMatch[1].toUpperCase();
        if (existingCodes.has(code)) continue;

        // Next two tds are fault location and probable cause
        const faultTd = i + 1 < tdArray.length ? tdArray[i + 1] : null;
        const causeTd = i + 2 < tdArray.length ? tdArray[i + 2] : null;

        // Make sure the next tds aren't themselves code cells
        const faultText = faultTd ? cleanText(faultTd.text()) : '';
        const causeText = causeTd ? cleanText(causeTd.text()) : '';

        codes.push({
            code,
            faultLocation: faultText,
            probableCause: causeText,
            detailUrl: codeLink.attr('href') || null,
        });
        existingCodes.add(code);

        // Skip past the consumed tds
        i += 2;
    }

    // Fallback: also pick up inline P1xxx codes listed as plain text
    // Pattern: >P1234 – Some description<
    const bodyHtml = $.html();
    const inlinePattern = /(?:^|>)\s*([PBCU]\d{4})\s*(?:&#8211;|–|-)\s*([^<\n]+)/gi;
    let inlineMatch;

    while ((inlineMatch = inlinePattern.exec(bodyHtml)) !== null) {
        const code = inlineMatch[1].toUpperCase();
        if (existingCodes.has(code)) continue;

        const description = cleanText(inlineMatch[2]);
        // Skip range headers like "P1100 – P1199 (Fuel and air metering)"
        if (description.match(/^[PBCU]\d{4}/i)) continue;
        if (description.match(/^\(/)) continue;

        codes.push({
            code,
            faultLocation: description,
            probableCause: '',
            detailUrl: null,
        });
        existingCodes.add(code);
    }

    return codes;
}

/**
 * Parse a generic codes page (e.g. /pcodes/, /bcodes/)
 * Same table structure as brand codes
 */
export function parseGenericCodesPage(html) {
    const $ = cheerio.load(html);
    const codes = [];

    const allTds = $('table td');
    const tdArray = [];
    allTds.each((_, el) => tdArray.push($(el)));

    for (let i = 0; i < tdArray.length; i++) {
        const cell = tdArray[i];
        const codeLink = cell.find('a');
        const rawText = (codeLink.text() || cell.text()).trim();

        const codeMatch = rawText.match(/^([PBCU]\w{4,5})\s*$/i);
        if (!codeMatch) continue;

        const code = codeMatch[1].toUpperCase();
        const faultTd = i + 1 < tdArray.length ? tdArray[i + 1] : null;
        const causeTd = i + 2 < tdArray.length ? tdArray[i + 2] : null;

        codes.push({
            code,
            faultLocation: faultTd ? cleanText(faultTd.text()) : '',
            probableCause: causeTd ? cleanText(causeTd.text()) : '',
            detailUrl: codeLink.attr('href') || null,
        });

        i += 2;
    }

    return codes;
}

/**
 * Parse an individual code detail page (e.g. /fordcodes/p0171/)
 * Extracts the full description, symptoms, causes, and fix info
 */
export function parseCodeDetailPage(html) {
    const $ = cheerio.load(html);

    const article = $('article').first();
    if (!article.length) return null;

    const title = article.find('h1, .entry-title').first().text().trim();

    // Content may be in .entry-content, or directly under <article>
    let content = article.find('.entry-content').first();
    if (!content.length) {
        content = article; // fallback: use article itself
    }

    const result = {
        title: cleanText(title),
        sections: {},
        fullText: '',
    };

    // Extract sections by headings
    let currentSection = 'overview';
    const sections = { overview: [] };

    content.children().each((_, el) => {
        const $el = $(el);
        const tagName = el.tagName?.toLowerCase();

        if (tagName === 'h2' || tagName === 'h3') {
            currentSection = cleanText($el.text())
                .toLowerCase()
                .replace(/[^a-z0-9\s]/g, '')
                .trim()
                .replace(/\s+/g, '_');
            sections[currentSection] = [];
        } else {
            const text = cleanText($el.text());
            if (text && !text.match(/^advertisement$/i)) {
                if (!sections[currentSection]) sections[currentSection] = [];
                sections[currentSection].push(text);
            }
        }
    });

    // Clean up sections
    for (const [key, lines] of Object.entries(sections)) {
        const joined = lines.join('\n').trim();
        if (joined) {
            result.sections[key] = joined;
        }
    }

    result.fullText = Object.values(result.sections).join('\n\n').trim();

    return result;
}

/**
 * Parse a brand home page (e.g. /ford/) for submodel links
 * Returns array of { url, title }
 */
export function parseBrandHomePage(html, brandSlug) {
    const $ = cheerio.load(html);
    const submodels = [];
    const seen = new Set();

    $(`a[href*="/${brandSlug}/"]`).each((_, el) => {
        const href = $(el).attr('href');
        const text = cleanText($(el).text());

        // Skip non-submodel links
        if (!href || href.includes('feed') || href.includes('amp') || href.includes('#')) return;
        if (href === `https://www.troublecodes.net/${brandSlug}/`) return;
        if (href === `/${brandSlug}/`) return;

        const url = href.startsWith('http') ? href : `https://www.troublecodes.net${href}`;
        if (seen.has(url)) return;
        seen.add(url);

        submodels.push({ url, title: text || url });
    });

    return submodels;
}

/**
 * Parse a submodel page for vehicle/engine/year info and DTC codes.
 *
 * Pages come in many HTML flavours:
 *   1. Lowercase <table> with <th> headers (newer OBD-II pages)
 *   2. Uppercase <TABLE> with bold <TD> headers (older OBD-II, e.g. Acura 3.5RL)
 *   3. Headerless 2-cell tables: numeric code | description (Jeep 89-95)
 *   4. 2-cell tables with 1-cell continuation rows (Chrysler 85-95)
 *   5. Multi-column cross-ref tables: code per ECU variant, desc last (BMW CEL)
 *   6. Empty <tr> rows before the real header row (Toyota, Honda)
 *   7. Multiple tables per page with section-header tables (Cadillac)
 *
 * Cheerio normalises everything to lowercase.
 */
export function parseSubmodelPage(html) {
    const $ = cheerio.load(html);
    const vehicles = [];
    const dtcCodes = [];
    const seenCodes = new Set();

    $('table').each((_, table) => {
        const $table = $(table);
        const rows = $table.find('tr');
        if (rows.length === 0) return;

        // ── Find header row ───────────────────────────────────────────
        // Scan all rows to find the first one that looks like a header.
        // It might be row 0, or row 4 (after empty rows), or absent.
        let headers = [];
        let dataRowStart = 0;

        for (let i = 0; i < Math.min(rows.length, 8); i++) {
            const $row = $(rows[i]);
            const thCells = $row.find('th');
            const tdCells = $row.find('td');

            // Option A: row has <th> cells
            if (thCells.length >= 2) {
                thCells.each((_, th) =>
                    headers.push(cleanText($(th).text()).toLowerCase())
                );
                dataRowStart = i + 1;
                break;
            }

            // Option B: row has <td> cells with header-like text
            if (tdCells.length >= 2) {
                const cellTexts = [];
                tdCells.each((_, td) =>
                    cellTexts.push(cleanText($(td).text()).toLowerCase())
                );
                const firstText = cellTexts[0];

                if (isHeaderKeyword(firstText)) {
                    headers = cellTexts;
                    dataRowStart = i + 1;
                    break;
                }

                // If this row has data (not empty), stop looking for headers
                if (firstText.length > 0) break;
            }

            // Empty row or single-cell row — skip and keep looking
        }

        // ── Classify table ────────────────────────────────────────────
        const hasModelColumn = headers.some((h) => h.includes('model'));
        const hasCodeColumn = headers.some((h) => isCodeHeaderKeyword(h));

        // Skip known non-code tables (diagnostic steps, mode descriptions)
        const hasFaultColumn = headers.some(
            (h) => /\b(fault|cause|probable|location|malfunction)\b/i.test(h)
        );
        const isInstructionTable = headers.some(
            (h) => /\b(step|action|range|units)\b/i.test(h)
        ) || (headers.some((h) => /\bmode\b/i.test(h)) && !hasFaultColumn);
        if (isInstructionTable) return; // skip this table (inside .each)

        // ── Walk data rows ────────────────────────────────────────────
        for (let i = dataRowStart; i < rows.length; i++) {
            const tds = $(rows[i]).find('td');
            if (tds.length === 0) continue;

            const cellTexts = [];
            tds.each((_, td) => cellTexts.push(cleanText($(td).text())));

            // Single-cell rows: continuation text or section header — skip
            if (tds.length === 1) continue;

            // ── Try to find a code in any cell ────────────────────────
            // Standard OBD-II code (P/B/C/U + 4-5 chars)
            let foundCode = null;
            let descriptionIdx = cellTexts.length - 1; // description is always last

            // Check first cell for OBD-II code (P/B/C/U) or Cadillac E-codes
            const obdMatch = cellTexts[0].match(/^([PBCUE]\w{3,5})$/i);
            if (obdMatch) {
                const raw = obdMatch[1].toUpperCase();
                // Standard P/B/C/U codes stay as-is; E-codes get prefixed
                if (/^[PBCU]/i.test(raw)) {
                    foundCode = raw;
                } else if (/^E\d{3}/i.test(raw)) {
                    foundCode = raw; // Cadillac E-codes (E012, E013, etc.)
                }
            }

            // Check first cell for numeric code (OBD-I / MIL)
            if (!foundCode) {
                const numMatch = cellTexts[0].match(/^(\d{1,4})$/);
                if (numMatch) {
                    const desc = cellTexts[descriptionIdx];
                    // Accept if: table is classified as code table, OR
                    // last cell has a real text description (not a number/year)
                    const descLooksLikeText =
                        desc && desc.length > 3 && !/^\d{4}/.test(desc);
                    if (hasCodeColumn || descLooksLikeText) {
                        foundCode = `MIL${numMatch[1]}`;
                    }
                }
            }

            // Multi-column tables (BMW): scan all cells for a numeric code
            // if first cell didn't match but last cell looks like a description
            if (!foundCode && cellTexts.length >= 3) {
                const lastCell = cellTexts[descriptionIdx];
                if (lastCell && lastCell.length > 3 && /[a-zA-Z]/.test(lastCell)) {
                    for (let c = 0; c < cellTexts.length - 1; c++) {
                        const m = cellTexts[c].match(/^(\d{4})$/);
                        if (m) {
                            foundCode = `MIL${m[1]}`;
                            break;
                        }
                    }
                }
            }

            // ── Emit the result ───────────────────────────────────────
            if (hasModelColumn && !hasCodeColumn && !foundCode) {
                // Vehicle info row
                const model = cellTexts[0];
                if (model && !model.match(/^(model|obd)/i)) {
                    vehicles.push({
                        model,
                        year: cellTexts[1] || '',
                        engine: cellTexts[2] || '',
                        system: cellTexts[3] || '',
                    });
                }
            } else if (foundCode) {
                // Deduplicate: same code can appear in multiple ECU columns
                const key = `${foundCode}:${cellTexts[descriptionIdx]}`;
                if (!seenCodes.has(key)) {
                    seenCodes.add(key);
                    dtcCodes.push({
                        code: foundCode,
                        faultLocation: cellTexts[descriptionIdx] || '',
                        probableCause: '',
                    });
                }
            }
        }
    });

    // ── Fallback: scan page for DTC codes outside tables ──────────────
    if (dtcCodes.length === 0) {
        const bodyHtml = $.html();
        const inlinePattern =
            /(?:^|>)\s*([PBCU]\d{4})\s*(?:&#8211;|–|-|<\/(?:b|strong)>)\s*(?:<[^>]*>)*\s*([^<\n]{3,})/gi;
        let m;
        while ((m = inlinePattern.exec(bodyHtml)) !== null) {
            const code = m[1].toUpperCase();
            const desc = cleanText(m[2]);
            const key = `${code}:${desc}`;
            if (seenCodes.has(key)) continue;
            seenCodes.add(key);
            dtcCodes.push({
                code,
                faultLocation: desc,
                probableCause: '',
            });
        }
    }

    const title = cleanText($('h1, .entry-title').first().text());
    return { title, vehicles, dtcCodes };
}

/**
 * Parse procedure/instruction content from a submodel page.
 * Extracts sections like "Accessing Trouble Codes", "Clearing Trouble Codes",
 * "Reset Service Intervals", diagnostic test procedures, etc.
 *
 * Returns array of { heading, steps[], type } where type is one of:
 *   'accessing', 'clearing', 'reset', 'test', 'general', 'other'
 */
export function parseProcedures(html) {
    const $ = cheerio.load(html);
    const procedures = [];

    const content = $('article .entry-content').first();
    if (!content.length) return procedures;

    // Walk children of .entry-content collecting sections by headings
    let currentHeading = null;
    let currentSteps = [];

    function flushSection() {
        if (currentHeading && currentSteps.length > 0) {
            procedures.push({
                heading: currentHeading,
                type: classifyProcedure(currentHeading),
                steps: currentSteps,
            });
        }
        currentSteps = [];
    }

    content.children().each((_, el) => {
        const $el = $(el);
        const tag = el.tagName?.toLowerCase();

        if (tag === 'h2' || tag === 'h3') {
            flushSection();
            const text = cleanText($el.text());
            // Only capture procedure-like headings
            if (isProcedureHeading(text)) {
                currentHeading = text;
            } else {
                currentHeading = null;
            }
        } else if (currentHeading) {
            // Collect content under a procedure heading
            if (tag === 'ul' || tag === 'ol') {
                $el.find('li').each((_, li) => {
                    const text = cleanText($(li).text());
                    if (text) currentSteps.push(text);
                });
            } else if (tag === 'p') {
                const text = cleanText($el.text());
                if (text && !text.match(/^advertisement$/i)) {
                    currentSteps.push(text);
                }
            } else if (tag === 'table') {
                // Some procedures have step/action tables
                const rows = $el.find('tr');
                rows.each((_, row) => {
                    const tds = $(row).find('td');
                    if (tds.length >= 2) {
                        const cells = [];
                        tds.each((_, td) => cells.push(cleanText($(td).text())));
                        // Step tables: "1 | Do something"
                        const isStep = cells[0].match(/^\d+$/);
                        if (isStep) {
                            currentSteps.push(cells.slice(1).join(' — '));
                        }
                    }
                });
            }
        }
    });

    flushSection();
    return procedures;
}

/** Check if heading text is a procedure section */
function isProcedureHeading(text) {
    // Exclude headings that are just code table labels ("Trouble Codes", "Fault Codes")
    // but NOT "Accessing Trouble Codes" or "Clearing Trouble Codes"
    if (/^(trouble\s+codes?|fault\s+codes?|dtc\s*codes?)\s*$/i.test(text.trim())) return false;

    // Use partial-word matches (access matches "Accessing", clear matches "Clearing", etc.)
    return /(access|clear|reset|self.?test|diagnos|retriev|eras(e|ing)|read.*code|locat|procedure|hookup|hook.?up|general\s+info|code\s+type|code\s+format|intermittent|ignition\s+timing|output\s+state|wiggle|cylinder\s+balance|memory\s+erase)/i.test(text)
        || /\btest(s|ing)?\b/i.test(text);
}

/** Classify procedure type from heading text */
function classifyProcedure(heading) {
    if (/access|retriev|read.*code|display|locat/i.test(heading)) return 'accessing';
    if (/clear|eras(e|ing)|delet/i.test(heading)) return 'clearing';
    if (/reset.*service|service.*reset|oil.*reset|interval/i.test(heading)) return 'reset';
    if (/general\s+info/i.test(heading)) return 'general';
    if (/test|hookup|hook.?up|diagnos|wiggle|cylinder|timing|output/i.test(heading)) return 'test';
    if (/code\s+type|code\s+format|intermittent/i.test(heading)) return 'reference';
    return 'other';
}

/** Check if text looks like a table header keyword */
function isHeaderKeyword(text) {
    return /\b(obd|code|model|scan|trouble|fault|mil|dtc|flash|malfunction|number|step|action|mode|range|units)\b/i.test(
        text
    );
}

/** Check if a header cell indicates a code-type column */
function isCodeHeaderKeyword(text) {
    return /\b(trouble.?code|obd|fault|scan.?code|mil|dtc|flash|code|malfunction|number)\b/i.test(
        text
    );
}

/**
 * Clean HTML entities and whitespace from text
 */
function cleanText(text) {
    return (text || '')
        .replace(/&#\d+;/g, (match) => {
            const code = parseInt(match.replace(/&#|;/g, ''));
            return String.fromCharCode(code);
        })
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/\s+/g, ' ')
        .trim();
}
