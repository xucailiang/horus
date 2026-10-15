(() => {
  const cards = [...document.querySelectorAll('.case')];
  let filter = 'all';
  const search = document.querySelector('#search');
  const apply = () => {
    const query = search.value.toLocaleLowerCase();
    let visible = 0;
    cards.forEach(card => {
      card.hidden = (filter !== 'all' && card.dataset.group !== filter) ||
        !card.textContent.toLocaleLowerCase().includes(query);
      if (!card.hidden) visible++;
    });
    document.querySelector('#empty').hidden = visible > 0;
  };
  document.querySelectorAll('[data-filter]').forEach(button => {
    button.addEventListener('click', () => {
      filter = button.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach(item =>
        item.setAttribute('aria-pressed', String(item === button)));
      apply();
    });
  });
  search.addEventListener('input', apply);
  document.querySelector('#expand').addEventListener('click', () => {
    cards.filter(card => !card.hidden).forEach(card =>
      card.querySelectorAll('details.action').forEach(detail => detail.open = true));
  });
  document.querySelector('#collapse').addEventListener('click', () => {
    document.querySelectorAll('#results details').forEach(detail => detail.open = false);
  });
  document.querySelectorAll('[data-reveal]').forEach(link => {
    link.addEventListener('click', () => {
      filter = 'all'; search.value = '';
      document.querySelectorAll('[data-filter]').forEach(button =>
        button.setAttribute('aria-pressed', String(button.dataset.filter === 'all')));
      apply();
      const detail = document.getElementById(link.getAttribute('href').slice(1));
      if (detail) detail.open = true;
    });
  });
  // Print the entire report, including sections hidden by filters or collapsed for reading.
  let restore = [];
  window.addEventListener('beforeprint', () => {
    restore = [...document.querySelectorAll('details,.case')].map(el => [el, el.open, el.hidden]);
    restore.forEach(([el]) => { if (el.tagName === 'DETAILS') el.open = true; el.hidden = false; });
  });
  window.addEventListener('afterprint', () => {
    restore.forEach(([el, open, hidden]) => { if (el.tagName === 'DETAILS') el.open = open; el.hidden = hidden; });
  });
  document.querySelector('#print').addEventListener('click', () => window.print());
})();
