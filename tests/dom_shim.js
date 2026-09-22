/* Minimal DOM so the page's own code can run headlessly. */
function El(id) {
  this.id = id; this.innerHTML = ""; this.textContent = ""; this.value = "";
  this.checked = false; this.dataset = {}; this.style = {}; this._attrs = {};
  this.children = []; this.className = "";
}
El.prototype.setAttribute = function (k, v) { this._attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return this._attrs[k]; };
El.prototype.addEventListener = function () {};
El.prototype.removeEventListener = function () {};
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.removeChild = function () {};
El.prototype.remove = function () {};
El.prototype.querySelectorAll = function () { return []; };
El.prototype.closest = function () { return null; };

var _els = {};
var document = {
  getElementById: function (id) { return _els[id] || (_els[id] = new El(id)); },
  querySelectorAll: function () { return []; },
  createElement: function (t) { return new El(t); },
  addEventListener: function () {},
  removeEventListener: function () {},
  body: new El("body")
};
var localStorage = {
  _d: {},
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null; },
  setItem: function (k, v) { this._d[k] = String(v); },
  removeItem: function (k) { delete this._d[k]; }
};
var location = { reload: function () {} };
function Blob() {}
var URL = { createObjectURL: function () { return "blob:test"; }, revokeObjectURL: function () {} };
function setTimeout() {}
function URLSearchParams(o) { this.o = o; }
URLSearchParams.prototype.toString = function () {
  var p = []; for (var k in this.o) p.push(k + "=" + encodeURIComponent(this.o[k]));
  return p.join("&");
};
