async function main() {
    const urls = [
        'https://www.troublecodes.net/acura/acura-3-5rl/',
        'https://www.troublecodes.net/ford/95-ford-explorer-4-0l/',
    ];
    for (const url of urls) {
        const resp = await fetch(url);
        const html = await resp.text();
        const title = html.match(/<title>([^<]+)/)?.[1] || 'none';
        console.log(url, '→ status', resp.status, '| title:', title);
    }
}
main().catch(e => console.error(e));
