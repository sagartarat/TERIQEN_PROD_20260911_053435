function togglePassword() {
    const p = document.getElementById('password');
    const btn = document.querySelector('.toggle');

    if (p.type === 'password') {
        p.type = 'text';
        btn.innerHTML = '🙈';
    } else {
        p.type = 'password';
        btn.innerHTML = '👁';
    }
}

document.querySelectorAll('input').forEach((el) => {
    el.addEventListener('blur', () => el.setAttribute('focused', 'true'));
});

document.getElementById('year').textContent = new Date().getFullYear();
