// Light/dark switch. The site follows the device until the reader picks a
// side; the choice is remembered in this browser, and picking the side the
// device already uses goes back to following the device.
(function () {
    var root = document.documentElement;
    var mq = matchMedia('(prefers-color-scheme: dark)');
    try {
        var saved = localStorage.getItem('theme');
        if (saved) root.dataset.theme = saved;
    } catch (e) {}

    function device() { return mq.matches ? 'dark' : 'light'; }
    function current() { return root.dataset.theme || device(); }

    document.addEventListener('DOMContentLoaded', function () {
        var button = document.querySelector('.theme-toggle');
        if (!button) return;
        function label() { button.textContent = current() === 'dark' ? 'Light' : 'Dark'; }
        button.hidden = false;
        label();
        button.addEventListener('click', function () {
            var next = current() === 'dark' ? 'light' : 'dark';
            try {
                if (next === device()) {
                    delete root.dataset.theme;
                    localStorage.removeItem('theme');
                } else {
                    root.dataset.theme = next;
                    localStorage.setItem('theme', next);
                }
            } catch (e) {}
            label();
        });
        mq.addEventListener('change', label);
    });
})();
