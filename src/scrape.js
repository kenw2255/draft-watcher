import { createHash } from 'node:crypto';
import * as cheerio from 'cheerio';

export async function scrapeDraftMenu(sourceUrl) {
  const response = await fetch(sourceUrl, {
    headers: {
      accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'accept-language': 'en-US,en;q=0.9',
      referer: 'https://www.sabatinis.com/bottleshop',
      'user-agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch draft menu: ${response.status} ${response.statusText}`);
  }

  const html = await response.text();
  const $ = cheerio.load(html);
  const title = cleanText($('.menu-title').first().text()) || 'Beers on Draft';
  const updatedAt = cleanText($('.date-time time').first().text());
  const sectionName = cleanText($('.section-name').first().text());
  const items = $('.menu-item')
    .map((_, element) => parseItem($, element))
    .get()
    .filter((item) => item.name);

  if (items.length === 0) {
    console.log(
      JSON.stringify(
        {
          diagnostic: 'Parsed zero menu items',
          sourceUrl,
          htmlLength: html.length,
          containsMenuItemClass: html.includes('menu-item'),
          containsUntappd: html.toLowerCase().includes('untappd'),
          titleTag: cleanText($('title').first().text()),
          bodyPreview: cleanText($('body').text()).slice(0, 500),
        },
        null,
        2
      )
    );
  }

  const lines = items.map(formatItemLine);
  const canonical = JSON.stringify({ title, updatedAt, sectionName, lines });
  const hash = createHash('sha256').update(canonical).digest('hex');

  return {
    sourceUrl,
    fetchedAt: new Date().toISOString(),
    title,
    updatedAt,
    sectionName,
    items,
    lines,
    hash,
  };
}

function parseItem($, element) {
  const item = $(element);
  const containers = item
    .find('.container-row')
    .map((_, row) => {
      const rowEl = $(row);
      return {
        type: cleanText(rowEl.find('.type').text()),
        price: cleanText(rowEl.find('.price').text()).replace(/\s+/g, ''),
      };
    })
    .get()
    .filter((container) => container.type || container.price);

  return {
    name: cleanText(item.find('.item-name a span').first().text() || item.find('.item-name a').first().text()),
    style: cleanText(item.find('.item-style .item-category').first().text()),
    abv: cleanText(item.find('.item-abv').first().text()),
    ibu: cleanText(item.find('.item-ibu').first().text()),
    brewery: cleanText(item.find('.brewery a').first().text() || item.find('.brewery').first().text()),
    location: cleanText(item.find('.item-brewery-location').first().text()),
    containers,
  };
}

function formatItemLine(item) {
  const details = [
    item.name,
    item.style,
    item.abv,
    item.ibu,
    item.brewery,
    item.location,
    item.containers.map((container) => `${container.type} ${container.price}`.trim()).join(', '),
  ].filter(Boolean);

  return details.join(' | ');
}

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}
