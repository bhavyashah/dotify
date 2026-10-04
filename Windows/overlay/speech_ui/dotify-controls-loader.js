(() => {
  let attempts = 0;
  function loadControls() {
    if (document.getElementById('dotify-controls-script')) return;
    const script = document.createElement('script');
    script.id = 'dotify-controls-script';
    script.src = 'http://127.0.0.1:8790/dotify-controls.js';
    script.async = true;
    script.onerror = () => {
      script.remove();
      attempts += 1;
      if (attempts < 30) setTimeout(loadControls, 500);
    };
    document.head.appendChild(script);
  }
  loadControls();
})();
