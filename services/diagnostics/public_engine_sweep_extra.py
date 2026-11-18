"""Additional public routes; imports only the isolated diagnostic runner."""
import public_engine_sweep as sweep
sweep.ENGINES={
'Wiby':('https://wiby.me/','q'),
'SearchMySite':('https://searchmysite.net/search/','q'),
'Lilo':('https://search.lilo.org/','q'),
'Good':('https://good-search.org/search','q'),
'OceanHero':('https://oceanhero.today/web','q'),
'You':('https://you.com/search','q'),
'Presearch':('https://presearch.com/search','q'),
'Boardreader':('https://boardreader.com/s/','q'),
'360 Search':('https://www.so.com/s','q'),
'Sogou':('https://www.sogou.com/web','query'),
'Daum':('https://search.daum.net/search','q'),
'Petal':('https://petalsearch.com/search','query'),
'Luxxle':('https://luxxle.com/search','q'),
}
if __name__=='__main__':sweep.main()
