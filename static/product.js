/* Keep the small-screen navigation compact; native details remains keyboard usable. */
const navigation = document.querySelector('details.navigation');
if (navigation && window.matchMedia('(max-width: 760px)').matches) navigation.open = false;
